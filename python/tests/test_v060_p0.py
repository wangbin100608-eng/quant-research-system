"""v0.6.0 P0 测试：股票池 PIT / 列投影门禁 / 流式审计 / 验收器修复。"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python/scripts"))

from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
from quant.timestamps_v04 import cutoff_for_year
from quant.data_quality_v05 import HistoricalUniverse


@pytest.fixture
def universe_parquet(tmp_path):
    """构造含未来退市信息的股票池数据。"""
    rows = [
        {"symbol": "OLD.SH", "market": "CN_A", "domain": "delisted",
         "available_time": "2001-06-01T00:00:00Z",
         "payload": json.dumps({"list_date": "1998-01-01", "delist_date": "2003-01-01",
                                "name": "老公司"})},
        {"symbol": "FUTURE.SH", "market": "CN_A", "domain": "delisted",
         "available_time": "2099-01-01T00:00:00Z",  # 未来才发布
         "payload": json.dumps({"list_date": "1999-01-01", "delist_date": "2001-01-01",
                                "name": "未来退市信息"})},
        {"symbol": "IDX.SH", "market": "CN_A", "domain": "index_member",
         "available_time": "2001-01-01T00:00:00Z",
         "payload": json.dumps({"index": "000300", "member": "600000"})},
    ]
    p = tmp_path / "universe.parquet"
    pq.write_table(pa.table({
        "symbol": [r["symbol"] for r in rows],
        "market": [r["market"] for r in rows],
        "domain": [r["domain"] for r in rows],
        "available_time": [r["available_time"] for r in rows],
        "payload": [r["payload"] for r in rows],
        "source": ["real:test"] * len(rows),
    }), str(p))
    return p


def test_universe_pit_excludes_future_delisting(universe_parquet, monkeypatch):
    import quant.data_quality_v05 as dq
    monkeypatch.setattr(dq, "resolve", lambda x: universe_parquet)
    u = dq.HistoricalUniverse(str(universe_parquet))
    snaps = u.build_snapshots(2002, 2002)
    s = snaps[2002]
    # 2099 才发布的退市信息不得进入 2002 股票池
    assert "FUTURE.SH" not in s["members"]
    assert s["cutoff"] == "2001-12-31T23:59:59+00:00"
    assert s["status"] in ("BLOCKED", "PARTIAL")
    assert "time_audit" in s and s["time_audit"]["records_admitted"] > 0


def test_universe_snapshot_reproducible_hash(universe_parquet):
    u = HistoricalUniverse(str(universe_parquet))
    a = u.build_snapshots(2002, 2002)
    b = HistoricalUniverse(str(universe_parquet)).build_snapshots(2002, 2002)
    ha = hashlib.sha256(json.dumps(a, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    hb = hashlib.sha256(json.dumps(b, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert ha == hb


def _sample(tmp_path, col="available_at"):
    p = tmp_path / f"s_{col}.parquet"
    pq.write_table(pa.table({
        "symbol": ["a", "b"], col: ["2001-06-01T00:00:00Z", "2003-01-01T00:00:00Z"],
        "close": [1.0, 2.0],
    }), str(p))
    return p


@pytest.mark.parametrize("columns", [["symbol"], ["symbol", "close"],
                                     ["available_at"], None, []])
def test_column_projection_does_not_bypass_gate(tmp_path, columns):
    p = _sample(tmp_path)
    reader = TimeGuardReader()
    table, audit = reader.read_parquet(p, cutoff_for_year(2002), columns=columns)
    assert table.num_rows == 1
    expected = sorted(columns) if columns else ["close", "symbol"]
    assert sorted(table.column_names) == expected
    batches = list(reader.stream_parquet(p, cutoff_for_year(2002),
                                         columns=columns, batch_size=1))
    assert sum(b.num_rows for b, _ in batches) == 1


def test_legacy_available_time_projection(tmp_path):
    p = _sample(tmp_path, col="available_time")
    reader = TimeGuardReader()
    t, _ = reader.read_parquet(p, cutoff_for_year(2002), columns=["symbol"])
    assert t.num_rows == 1 and t.column_names == ["symbol"]


def test_missing_time_column_strict_reject(tmp_path):
    p = tmp_path / "no_time.parquet"
    pq.write_table(pa.table({"symbol": ["a"], "close": [1.0]}), str(p))
    reader = TimeGuardReader()
    with pytest.raises(TimeGuardReadError):
        reader.read_parquet(p, cutoff_for_year(2002), columns=["symbol"])
    with pytest.raises(TimeGuardReadError):
        list(reader.stream_parquet(p, cutoff_for_year(2002), columns=["symbol"]))


def test_stream_audit_per_batch_and_cumulative(tmp_path):
    days = ["2001-01-01T00:00:00Z", "2003-01-01T00:00:00Z"] * 5
    p = tmp_path / "audit.parquet"
    pq.write_table(pa.table({"symbol": [f"S{i}" for i in range(10)],
                             "available_at": days, "v": list(range(10))}), str(p))
    batches = list(TimeGuardReader().stream_parquet(p, cutoff_for_year(2002), batch_size=2))
    assert len(batches) == 5
    for i, (_, audit) in enumerate(batches):
        assert audit.batch_index == i
        assert audit.rows_in == 2 and audit.rows_out == 1
        assert audit.dropped_future == 1 and audit.dropped_unknown == 0
        assert audit.cumulative_rows_in == 2 * (i + 1)
        assert audit.cumulative_rows_out == i + 1
        assert audit.file_sha256 and audit.file_size_bytes > 0
        assert audit.elapsed_seconds >= 0


def test_acceptance_historical_report_missing_fails(tmp_path, monkeypatch):
    import final_acceptance_v05 as fa
    a = fa.Acceptance05()
    monkeypatch.setattr(fa, "ROOT", tmp_path)  # 无 test_report_v03.json
    ok, reason = a.report_tamper_matrix()
    assert ok is False and "缺失" in reason
