"""v0.4.0 P0 test_time_v04：UTC 时间内核 + TimeGuardReader + Rust/Python 一致性 + 负向测试。"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant.timestamps_v04 import (cutoff_for_year, cutoff_iso, to_utc, iso_utc,
                                  migrate_record, available_ok, UTC)
from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
from quant.contracts import PredictionSpec
from quant.utils import ROOT


def test_cutoff_is_utc_235959():
    c = cutoff_for_year(2002)
    assert c == datetime(2001, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert cutoff_iso(2003) == "2002-12-31T23:59:59+00:00"


def test_to_utc_market_timezone():
    # 中国 2002-01-01 08:00 = UTC 2002-01-01 00:00
    dt = to_utc(datetime(2002, 1, 1, 8, 0), "CN_A")
    assert dt == datetime(2002, 1, 1, 0, 0, tzinfo=UTC)
    # 美股东部 2002-01-01 09:30 EST(UTC-5) = 14:30 UTC
    dt2 = to_utc(datetime(2002, 1, 1, 9, 30), "US")
    assert dt2 == datetime(2002, 1, 1, 14, 30, tzinfo=UTC)
    # Z 后缀 aware
    assert to_utc("2002-01-01T00:00:00Z") == datetime(2002, 1, 1, tzinfo=UTC)


def test_migrate_legacy_fields():
    r = migrate_record({"available_time": "2001-12-31", "published_time": "2001-12-30",
                        "collected_time": "2002-01-02", "symbol": "X"})
    assert r["available_at"] == "2001-12-31T00:00:00+00:00"
    assert r["published_at"] == "2001-12-30T00:00:00+00:00"
    assert r["ingested_at"] == "2002-01-02T00:00:00+00:00"
    assert r["schema_v04"] is True
    # 旧字段保留以便回滚
    assert r["available_time"] == "2001-12-31"


def test_available_ok_strict_utc():
    cutoff = cutoff_for_year(2002)
    assert available_ok("2001-12-31T23:59:59Z", cutoff)
    assert not available_ok("2002-01-01T00:00:00Z", cutoff)
    assert not available_ok(None, cutoff)
    # 时区转换后越界
    assert not available_ok(datetime(2002, 1, 1, 8, 0), cutoff, "CN_A")


def test_reader_filters_future_and_unknown(tmp_path):
    t = pa.table({
        "symbol": ["a", "b", "c"],
        "available_at": ["2001-12-31T23:59:59Z", "2002-05-01T00:00:00Z", "2001-06-01T00:00:00Z"],
        "value": [1.0, 2.0, 3.0],
    })
    p = tmp_path / "sample.parquet"
    pq.write_table(t, str(p))
    reader = TimeGuardReader()
    spec = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")
    out, audit = reader.read_parquet(p, spec)
    assert out.num_rows == 2
    assert audit.dropped_future == 1
    assert audit.rows_in == 3 and audit.rows_out == 2


def test_reader_rejects_unknown_and_no_time_column(tmp_path):
    reader = TimeGuardReader()
    spec = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")
    t = pa.table({"symbol": ["a"], "available_at": [None], "v": [1.0]})
    p = tmp_path / "unknown.parquet"
    pq.write_table(t, str(p))
    with pytest.raises(TimeGuardReadError):
        reader.read_parquet(p, spec)
    t2 = pa.table({"symbol": ["a"], "v": [1.0]})
    p2 = tmp_path / "notime.parquet"
    pq.write_table(t2, str(p2))
    with pytest.raises(TimeGuardReadError):
        reader.read_parquet(p2, spec)


def test_reader_accepts_legacy_available_time(tmp_path):
    t = pa.table({"symbol": ["a", "b"], "available_time": ["2001-01-01", "2003-01-01"], "v": [1, 2]})
    p = tmp_path / "legacy.parquet"
    pq.write_table(t, str(p))
    reader = TimeGuardReader()
    out, audit = reader.read_parquet(p, datetime(2001, 12, 31, 23, 59, 59, tzinfo=UTC))
    assert out.num_rows == 1 and audit.dropped_future == 1


def test_rust_python_cutoff_consistency():
    """Rust 与 Python 对同一 cutoff 的判定必须一致。"""
    spec = PredictionSpec.for_year(2002, 2000, "asof_2001", "t", "f")
    cutoff_py = cutoff_iso(2002)
    import subprocess
    p = ROOT / "rust_core/target/release/timeguard"
    if not p.exists():
        pytest.skip("rust binary not built")
    snap = ROOT / "data/knowledge/snapshots/asof_2001"
    # Python 读取快照时间索引全部 <= cutoff 应有 0 未来
    from quant.timestamps_v04 import to_utc
    cutoff = cutoff_for_year(2002)
    future = 0
    for tf in snap.rglob("*.timeindex"):
        for line in tf.read_text().splitlines():
            v = line.strip()
            if v and v.lower() not in ("unknown", "null", "none"):
                dt = to_utc(v)
                if dt is None or dt > cutoff:
                    future += 1
    assert future == 0
    # Rust 审计同一快照必须 PASS
    r = subprocess.run([str(p), "audit", "--snapshot", str(snap),
                        "--cutoff", cutoff.date().isoformat(), "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 0
