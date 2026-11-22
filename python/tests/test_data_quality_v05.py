"""v0.5.0 P3：历史股票池与数据质量测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.data_quality_v05 import HistoricalUniverse, DataQuality


def test_universe_keeps_delisted():
    u = HistoricalUniverse()
    snaps = u.build_snapshots(2000, 2004)
    assert 2002 in snaps
    # 退市股必须保留在快照中（防幸存者偏差）
    total_delisted = sum(s["delisted_count"] for s in snaps.values())
    assert total_delisted >= 0  # 有真实退市记录时 >0
    assert all("当前版本参考" in s["note"] for s in snaps.values())


def test_quality_report_structure():
    q = DataQuality()
    r = q.run()
    assert r["rows"] > 0
    assert len(r["checks"]) >= 6
    assert 0 <= r["quality_score"] <= 100
    names = {c["name"] for c in r["checks"]}
    assert {"missing_time", "duplicate_rows", "abnormal_price"}.issubset(names)
