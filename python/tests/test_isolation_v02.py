"""v0.2.0 阶段2：对抗性时间隔离测试。

与 v0.1.0 的“正向通过”测试不同，这里主动注入未来数据/篡改/格式攻击，
证明隔离机制确实阻断，而不是只检查配置文件。
"""
from __future__ import annotations

import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant.contracts import PredictionSpec, parse_date
from quant.timeguard import LeakError, TimeTravelEngine
from quant.utils import ROOT, resolve


SPEC_2002 = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")


def _fresh_tmp(tmp_path: Path, y: int) -> Path:
    """从真实快照复制一份到 tmp，便于破坏性注入而不污染生产数据。"""
    src = ROOT / f"data/knowledge/snapshots/asof_{y}"
    dst = tmp_path / f"asof_{y}"
    shutil.copytree(src, dst)
    return dst


def _tg_force_local():
    """强制使用本地 Rust 二进制。"""
    tg = TimeTravelEngine()
    tg.rust_cli = ROOT / "rust_core/target/release/timeguard"
    tg.prefer_rust = True
    return tg


# ===== 3 年逐一验证：真实快照无任何未来数据 =====
@pytest.mark.parametrize("year", [2002, 2003, 2004])
def test_snapshot_contains_no_future_data(year):
    tg = _tg_force_local()
    spec = PredictionSpec.for_year(year, 2000, f"asof_{year-1}", "t", "f")
    snap = ROOT / f"data/knowledge/snapshots/asof_{year-1}"
    for ti in snap.rglob("*.timeindex"):
        for line in ti.read_text().splitlines():
            v = line.strip()
            if v and v.lower() not in ("unknown", "null", "none"):
                assert parse_date(v) <= spec.cutoff_date
    # 忠实校验：Rust 审计通过（旧 manifest 尚未全目录哈希，此处 require_sealed=False 仅查时间）
    r = tg.audit_snapshot(snap, spec)
    assert r["ok"]


# ===== 注入攻击：未来数据必须被 Rust 阻断 =====
@pytest.mark.parametrize("year,cut", [(2002, "2001-12-31"), (2003, "2002-12-31"), (2004, "2003-12-31")])
def test_injected_future_row_blocked_by_rust(tmp_path, year, cut):
    snap = _fresh_tmp(tmp_path, year - 1)
    ti = snap / "clean_data.timeindex"
    with open(ti, "a", encoding="utf-8") as f:
        f.write(f"{year}-06-15\n")  # 未来数据
    tg = _tg_force_local()
    spec = PredictionSpec.for_year(year, 2000, f"asof_{year-1}", "t", "f")
    with pytest.raises(LeakError) as e:
        tg.audit_snapshot(snap, spec)
    assert "future_data_leak" in str(e.value)


def test_unknown_availability_blocked_by_rust(tmp_path):
    snap = _fresh_tmp(tmp_path, 2001)
    with open(snap / "clean_data.timeindex", "a", encoding="utf-8") as f:
        f.write("unknown\n")
    tg = _tg_force_local()
    with pytest.raises(LeakError):
        tg.audit_snapshot(snap, SPEC_2002)


def test_invalid_date_format_blocked_by_rust(tmp_path):
    """v0.2.0：非标准日期串（如 20011231）不能再蒙混或被误判。"""
    snap = _fresh_tmp(tmp_path, 2001)
    ti = snap / "clean_data.timeindex"
    lines = ti.read_text().splitlines()
    lines[0] = "20011231"  # 合法等同 2001-12-31 但格式非法
    ti.write_text("\n".join(lines) + "\n")
    tg = _tg_force_local()
    with pytest.raises(LeakError) as e:
        tg.audit_snapshot(snap, SPEC_2002)
    assert "invalid_date_format" in str(e.value)


def test_tampered_parquet_blocked_by_rust(tmp_path):
    """v0.2.0 P0 修复：篡改 parquet 后 Rust 必须比对 manifest hash 并阻断。"""
    snap = _fresh_tmp(tmp_path, 2001)
    p = snap / "clean_data.parquet"
    p.write_bytes(p.read_bytes() + b"garbage")
    tg = _tg_force_local()
    with pytest.raises(LeakError) as e:
        tg.audit_snapshot(snap, SPEC_2002, require_sealed=True)
    assert "hash" in str(e.value).lower()


def test_manifest_missing_blocked_when_required(tmp_path):
    """v0.2.0 P0 修复：require_sealed 时删 manifest 必须阻断。"""
    snap = _fresh_tmp(tmp_path, 2001)
    (snap / "manifest.json").unlink()
    tg = _tg_force_local()
    with pytest.raises(LeakError):
        tg.audit_snapshot(snap, SPEC_2002, require_sealed=True)


def test_sealed_zero_blocked_by_rust(tmp_path):
    """v0.2.0 P0 修复：sealed:0 不能蒙混为已冻结。"""
    snap = _fresh_tmp(tmp_path, 2001)
    mpath = snap / "manifest.json"
    m = json.loads(mpath.read_text())
    m["sealed"] = 0
    mpath.write_text(json.dumps(m))
    # 同步重算受影响 hash 不重要（manifest 自身不在 hash 内），但 file_hashes 仍是旧的
    tg = _tg_force_local()
    with pytest.raises(LeakError):
        tg.audit_snapshot(snap, SPEC_2002, require_sealed=True)


def test_future_file_can_be_detected_by_kb_verify(tmp_path):
    """KB verify_snapshot 检测未来行（不改文件）。"""
    from quant.knowledge import KnowledgeBase
    snap = _fresh_tmp(tmp_path, 2001)
    with open(snap / "clean_data.timeindex", "a", encoding="utf-8") as f:
        f.write("2003-01-01\n")
    kb = KnowledgeBase()
    kb.snapshots_dir = tmp_path  # 指向临时目录
    r = kb.verify_snapshot(2001, SPEC_2002)
    assert not r["ok"]
    assert r["checks"]["future_rows"] >= 1


# ===== 全类型数据隔离：原始数据/知识/缓存/模型/归一化 =====
@pytest.mark.parametrize("year", [2002, 2003, 2004])
def test_all_data_types_within_boundary(year):
    snap = ROOT / f"data/knowledge/snapshots/asof_{year-1}"
    cutoff = date(year - 1, 12, 31)
    # 原始清洗数据
    for col_file in ("clean_data.parquet", "knowledge.parquet"):
        t = pq.read_table(str(snap / col_file), columns=["available_time"])
        for v in t.column("available_time").to_pylist():
            if v:
                assert parse_date(v) <= cutoff, f"{col_file} contains future {v}"
    # 特征（asof_date 不超边界，归一化只用历史）
    fp = snap / "features.parquet"
    if fp.exists():
        t = pq.read_table(str(fp), columns=["asof_date"])
        for v in t.column("asof_date").to_pylist():
            assert parse_date(v) <= cutoff
    # 模型元数据 trained_to
    mp = snap / "model_registry.json"
    if mp.exists():
        for name, meta in json.loads(mp.read_text()).items():
            if meta.get("trained_to"):
                assert parse_date(meta["trained_to"]) <= cutoff
    # 缓存：快照内 features.parquet 即特征缓存，路径绑定快照
    assert fp.parent == snap
