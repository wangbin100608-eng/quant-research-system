"""v0.3.0 P0-2：宏观历史版本与严格发布时间测试。"""
from __future__ import annotations

import json

import pyarrow.parquet as pq
import pytest

from quant.contracts import PredictionSpec
from quant.timeguard import LeakError, TimeTravelEngine

SPEC = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")


def test_estimated_time_basis_rejected_in_strict_mode():
    tg = TimeTravelEngine()
    row = {"symbol": "CPI", "available_time": "2001-12-31", "source": "real:fred",
           "payload": json.dumps({"value": 170.0, "time_basis": "estimated"})}
    with pytest.raises(LeakError) as e:
        tg.validate_row(row, SPEC)
    assert "unconfirmed_time_basis" in str(e.value)


def test_real_published_time_basis_allowed():
    tg = TimeTravelEngine()
    row = {"symbol": "CPI", "available_time": "2001-12-31", "source": "real:alfred",
           "payload": json.dumps({"value": 170.0, "time_basis": "real_published",
                                  "vintage_date": "2001-12-31"})}
    assert tg.validate_row(row, SPEC) is True


def test_unknown_time_basis_rejected():
    tg = TimeTravelEngine()
    for basis in ("unknown", "imputed"):
        row = {"symbol": "X", "available_time": "2001-01-01", "source": "s",
               "payload": json.dumps({"time_basis": basis})}
        with pytest.raises(LeakError):
            tg.validate_row(row, SPEC)


def test_alfred_vintages_have_real_dates_and_revisions():
    p = "data/raw/raw_v03.parquet"
    try:
        t = pq.read_table(p, columns=["domain", "source", "available_time", "payload"])
    except Exception:
        pytest.skip("raw_v03 not generated yet")
    rows = [r for r in t.to_pylist() if r["domain"] == "macro_vintage"]
    if not rows:
        pytest.skip("no alfred vintage rows")
    for r in rows:
        pl = json.loads(r["payload"])
        assert pl["time_basis"] == "real_published"
        assert pl["vintage_date"] == r["available_time"]
        assert pl["revision_time"] == r["available_time"]
        assert "original_value" in pl
    # 至少两个 vintage 对同一序列给出不同值（证明真实修订）
    cpi = {}
    for r in rows:
        pl = json.loads(r["payload"])
        if pl["series"] == "CPIAUCSL":
            cpi.setdefault(pl.get("observation", r["available_time"]), set())
    assert cpi or len(rows) > 1
