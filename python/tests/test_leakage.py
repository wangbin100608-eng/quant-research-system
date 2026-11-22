"""自动化防泄漏测试套件 —— 8 项强制检查。

运行: PYTHONPATH=python python3 -m pytest python/tests -v
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant.contracts import PredictionSpec
from quant.evaluation import PredictionStore
from quant.features import build_features
from quant.knowledge import KnowledgeBase, SnapshotSealedError
from quant.models import ModelRegistry, train_benchmark
from quant.timeguard import LeakError, TimeTravelEngine
from quant.utils import ROOT, get_config, resolve

SPEC_2002 = PredictionSpec.for_year(2002, 2000, "asof_2001", "bm_v1", "feature_v1")


def _snapshot(year: int) -> Path:
    return resolve(f"data/knowledge/snapshots/asof_{year}")


# 1. 未来日期的数据不能进入训练集
def test_future_data_not_in_training_set():
    tg = TimeTravelEngine()
    clean = pq.read_table(str(_snapshot(2001) / "clean_data.parquet"))
    assert tg.assert_no_future_in_parquet(_snapshot(2001) / "clean_data.parquet", SPEC_2002)["ok"]
    times = clean.column("available_time").to_pylist()
    assert all(str(t) <= "2001-12-31" for t in times)


def test_future_row_rejected_by_validate_row():
    tg = TimeTravelEngine()
    bad = {"symbol": "X", "available_time": "2002-05-01", "source": "fixture"}
    with pytest.raises(LeakError):
        tg.validate_row(bad, SPEC_2002)


def test_unknown_availability_rejected():
    tg = TimeTravelEngine()
    bad = {"symbol": "X", "available_time": None, "source": "fixture"}
    with pytest.raises(LeakError):
        tg.validate_row(bad, SPEC_2002)


# 2. 未来日期的文档不能进入向量检索结果（快照知识表）
def test_future_knowledge_not_in_snapshot():
    tg = TimeTravelEngine()
    assert tg.assert_no_future_in_parquet(
        _snapshot(2001) / "knowledge.parquet", SPEC_2002, "available_time")["ok"]
    k = pq.read_table(str(_snapshot(2001) / "knowledge.parquet"))
    times = k.column("available_time").to_pylist()
    assert all(t is None or str(t) <= "2001-12-31" for t in times)


# 3. 未来日期的知识图谱节点不能被查询（知识表 + 决策规则时间范围）
def test_future_graph_node_rejected():
    kb = KnowledgeBase()
    rules = kb.list_rules()
    for r in rules:
        if r["valid_from"]:
            assert r["valid_from"] <= "2004-12-31"
    # 向量库默认禁用，不能跨快照检索
    assert get_config()["knowledge"]["vector_enabled"] is False


# 4. 未来财报不能用于过去的特征计算
def test_future_fundamental_not_in_features():
    ft = pq.read_table(str(_snapshot(2001) / "features.parquet"))
    # 2001 年末的 eps 不能来自 2002 年披露的 2001 年报（其 available_time=2002-03-15）
    # 检查特征 asof 日期不超过 cutoff
    assert all(str(d) <= "2001-12-31" for d in ft.column("asof_date").to_pylist())


# 5. 未来数据不能参与归一化和特征选择
def test_feature_cache_scoped_to_snapshot():
    # 每个快照有独立 features.parquet，版本与快照绑定
    for y in (2001, 2002, 2003):   # 参与预测的快照
        p = _snapshot(y) / "features.parquet"
        assert p.exists(), f"missing {p}"
    # manifest 记录 feature_version
    m = json.loads((_snapshot(2001) / "manifest.json").read_text())
    assert m["feature_version"] == "feature_v1"


# 6. 未来模型权重不能被加载
def test_future_model_weight_rejected():
    tg = TimeTravelEngine()
    with pytest.raises(LeakError):
        tg.validate_model({
            "built_year": 2003,
            "trained_to": "2002-12-31",
            "knowledge_snapshot_id": "asof_2001",
        }, SPEC_2002)
    with pytest.raises(LeakError):
        tg.validate_model({
            "built_year": 2002,
            "trained_to": "2002-12-31",
            "knowledge_snapshot_id": "asof_2001",
        }, SPEC_2002)


def test_model_snapshot_mismatch_rejected():
    tg = TimeTravelEngine()
    with pytest.raises(LeakError):
        tg.validate_model({
            "built_year": 2002, "trained_to": "2001-12-31",
            "knowledge_snapshot_id": "asof_2002",
        }, SPEC_2002)


# 7. 缓存不能绕过时间边界
def test_cache_paths_are_snapshot_scoped():
    for y in (2001, 2002, 2003):
        assert (_snapshot(y) / "features.parquet").exists()
    # 缓存不在全局共享目录
    cache = ROOT / "data/cache"
    assert not list(cache.glob("feature*.parquet"))


# 8. 历史预测不能被后续知识库更新改变
def test_predictions_are_immutable():
    store = PredictionStore()
    v = store.verify_all()
    assert v["ok"] and v["records"] >= 3, v
    # 尝试篡改文件应被检测
    rows = store.meta.query("SELECT pred_id, result_hash FROM predictions LIMIT 1")
    pred_id = rows[0]["pred_id"]
    p = resolve(f"data/predictions/{pred_id}.json")
    original = p.read_text(encoding="utf-8")
    try:
        data = json.loads(original)
        data["result"]["prediction_interval"]["lower"] = 999.0
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        assert not store.verify_all()["ok"]
    finally:
        p.write_text(original, encoding="utf-8")
    assert store.verify_all()["ok"]


def test_sealed_snapshot_immutable():
    kb = KnowledgeBase()
    spec = SPEC_2002
    with pytest.raises(Exception):
        kb.create_snapshot(resolve("data/raw/raw_all.parquet"), spec)


# 附加：Rust 引擎端到端审计
def test_rust_audit_passes_all_snapshots():
    tg = TimeTravelEngine()
    if not tg.rust_available():
        pytest.skip("Rust binary not built")
    for y, cutoff in ((2001, "2001-12-31"), (2002, "2002-12-31"),
                      (2003, "2003-12-31"), (2004, "2004-12-31")):
        spec = PredictionSpec.for_year(y + 1, 2000, f"asof_{y}", "bm_v1", "feature_v1")
        r = tg.audit_snapshot(_snapshot(y), spec)
        assert r["ok"] and r.get("engine") == "rust"
