"""v0.5.0 P2：流式 TimeGuardReader 测试（分批一致性、低内存、未来数据）。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path = __import__("pathlib").Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
from quant.timestamps_v04 import cutoff_for_year


@pytest.fixture
def sample(tmp_path):
    n = 5000
    days = ["2001-06-01T00:00:00Z"] * (n // 2) + ["2003-01-01T00:00:00Z"] * (n // 2)
    t = pa.table({"symbol": [f"S{i}" for i in range(n)],
                  "available_at": days,
                  "v": list(range(n))})
    p = tmp_path / "sample.parquet"
    pq.write_table(t, str(p))
    return p, n


def test_stream_matches_batch_read(sample):
    p, n = sample
    cutoff = cutoff_for_year(2002)
    reader = TimeGuardReader()
    out1, audit1 = reader.read_parquet(p, cutoff)
    batches = list(reader.stream_parquet(p, cutoff, batch_size=512))
    rows = sum(b.num_rows for b, _ in batches)
    assert rows == out1.num_rows == n // 2
    assert all(b.num_rows <= 512 for b, _ in batches if b.num_rows > 0)


def test_stream_different_batch_sizes(sample):
    p, _ = sample
    cutoff = cutoff_for_year(2002)
    reader = TimeGuardReader()
    counts = []
    for bs in (128, 1024, 8192):
        batches = list(reader.stream_parquet(p, cutoff, batch_size=bs))
        counts.append(sum(b.num_rows for b, _ in batches))
    assert len(set(counts)) == 1 and counts[0] > 0


def test_stream_rejects_future_and_unknown(tmp_path):
    p = tmp_path / "bad.parquet"
    pq.write_table(pa.table({"symbol": ["a", "b"],
                             "available_at": ["2099-01-01T00:00:00Z", None],
                             "v": [1, 2]}), str(p))
    reader = TimeGuardReader()
    with pytest.raises(TimeGuardReadError):
        list(reader.stream_parquet(p, cutoff_for_year(2002), batch_size=1))


def test_stream_no_time_column_strict(tmp_path):
    p = tmp_path / "notime.parquet"
    pq.write_table(pa.table({"symbol": ["a"], "v": [1]}), str(p))
    with pytest.raises(TimeGuardReadError):
        list(TimeGuardReader().stream_parquet(p, cutoff_for_year(2002)))
