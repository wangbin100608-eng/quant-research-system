"""v0.2.0 阶段4：知识库升级测试（恢复/重新验证/来源/严格模式）。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant.contracts import PredictionSpec
from quant.knowledge import KnowledgeBase, SnapshotSealedError
from quant.knowledge.kb import hash_snapshot_dir
from quant.timeguard import LeakError
from quant.utils import ROOT

SPEC = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")


def test_verify_snapshot_detects_tamper(tmp_path):
    kb = KnowledgeBase()
    kb.snapshots_dir = tmp_path
    import shutil
    dst = tmp_path / "asof_2001"
    shutil.copytree(ROOT / "data/knowledge/snapshots/asof_2001", dst)
    # 原 manifest 是 v0.1 的 5 项 hash；verify 会以全目录对比，先把 manifest 更新为全目录基线
    m = json.loads((dst / "manifest.json").read_text())
    m["file_hashes"] = hash_snapshot_dir(dst)
    (dst / "manifest.json").write_text(json.dumps(m))
    ok = kb.verify_snapshot(2001, SPEC)
    assert ok["ok"]
    # 篡改一个文件
    (dst / "knowledge.timeindex").write_text("bad-data\n")
    bad = kb.verify_snapshot(2001, SPEC)
    assert not bad["ok"]
    assert "knowledge.timeindex" in bad["checks"]["mismatched_files"]


def test_verify_snapshot_detects_future_rows(tmp_path):
    import shutil
    kb = KnowledgeBase()
    kb.snapshots_dir = tmp_path
    dst = tmp_path / "asof_2001"
    shutil.copytree(ROOT / "data/knowledge/snapshots/asof_2001", dst)
    with open(dst / "clean_data.timeindex", "a") as f:
        f.write("2002-07-01\n")
    r = kb.verify_snapshot(2001, SPEC)
    assert not r["ok"]
    assert r["checks"]["future_rows"] >= 1


def test_restore_snapshot_from_backup(tmp_path):
    import shutil
    kb = KnowledgeBase()
    kb.snapshots_dir = tmp_path
    backup = tmp_path.parent / "backup"
    backup.mkdir(exist_ok=True)
    shutil.copytree(ROOT / "data/knowledge/snapshots/asof_2001", backup / "asof_2001")
    m = json.loads((backup / "asof_2001/manifest.json").read_text())
    m["file_hashes"] = hash_snapshot_dir(backup / "asof_2001")
    (backup / "asof_2001/manifest.json").write_text(json.dumps(m))
    result = kb.restore_snapshot_from_manifest(2001, backup)
    assert result["ok"]


def test_knowledge_entries_have_source_and_available_time():
    pool = ROOT / "data/knowledge/raw_documents/knowledge_pool.parquet"
    if not pool.exists():
        pytest.skip("no knowledge pool yet")
    t = pq.read_table(str(pool), columns=["source", "available_time"])
    rows = t.to_pylist()
    assert rows
    for r in rows:
        assert r["source"], "每条知识必须有来源"
        assert r["available_time"], "每条知识必须有 available_time"


def test_strict_mode_rejects_unknown_knowledge():
    kb = KnowledgeBase()
    tmp = ROOT / "data/cache/_kb_test"
    import shutil
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    kb.snapshots_dir = tmp
    raw = ROOT / "data/raw/raw_fixture.parquet"
    if not raw.exists():
        raw = ROOT / "data/raw/raw_all.parquet"
    spec = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")
    bad_knowledge = [{"entry_id": "bad", "domain": "knowledge", "symbol": "X",
                      "event_time": "2001-01-01", "published_time": None,
                      "available_time": None, "collected_time": "2001-01-01",
                      "revision_time": None, "source": "test", "content": "unknown time"}]
    with pytest.raises(LeakError):
        kb.create_snapshot(raw, spec, bad_knowledge)


def test_sealed_snapshot_cannot_be_recreated():
    kb = KnowledgeBase()
    raw = ROOT / "data/raw/raw_fixture.parquet"
    if not raw.exists():
        raw = ROOT / "data/raw/raw_all.parquet"
    with pytest.raises(SnapshotSealedError):
        kb.create_snapshot(raw, SPEC)
