"""v0.5.0 P2：流式读取性能基准（100万行模拟，记录 RSS/耗时/一致性）。

在 /tmp 生成大规模 Parquet（不写入项目 data/），比较：
  * baseline: pq.read_table 全量 + to_pylist 过滤
  * stream: TimeGuardReader.stream_parquet(iter_batches)
输出 data/reports/stream_performance_v05.json（新文件）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from quant.utils import ROOT, measure, resolve
from quant.timeguard_reader import TimeGuardReader
from quant.timestamps_v04 import cutoff_for_year


def make_data(path: Path, n: int, batch=100_000):
    pf = None
    for i in range(0, n, batch):
        m = min(batch, n - i)
        av = ["2001-06-01T00:00:00Z"] * (m // 2) + ["2003-01-01T00:00:00Z"] * (m - m // 2)
        t = pa.table({"symbol": [f"S{(i+j)%1000}" for j in range(m)],
                      "available_at": av, "v": [float(j) for j in range(m)]})
        if pf is None:
            pf = pq.ParquetWriter(str(path), t.schema, compression="zstd")
        pf.write_table(t)
    pf.close()


def main():
    tmp = Path(tempfile.mkdtemp(prefix="v05_stream_"))
    p = tmp / "big.parquet"
    n = 1_000_000
    t0 = time.perf_counter()
    make_data(p, n)
    gen_s = round(time.perf_counter() - t0, 2)
    cutoff = cutoff_for_year(2002)
    reader = TimeGuardReader()

    with measure("baseline_full_load") as st1:
        table = pq.read_table(str(p))
        av = table.column("available_at").to_pylist()
        keep = [i for i, v in enumerate(av) if v <= "2001-12-31T23:59:59+00:00"]
        n1 = len(keep)

    with measure("stream_batches") as st2:
        n2 = 0
        batches = 0
        for batch, _audit in reader.stream_parquet(p, cutoff, batch_size=65_536):
            n2 += batch.num_rows
            batches += 1

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "v0.5.0", "rows": n, "data_generation_seconds": gen_s,
        "disk_mb": round(p.stat().st_size / 1024 / 1024, 2),
        "baseline": {"rows_out": n1, **{k: st1[k] for k in ("elapsed_seconds", "mem_peak_mb", "rss_mb", "rss_delta_mb")}},
        "stream": {"rows_out": n2, "batches": batches,
                   **{k: st2[k] for k in ("elapsed_seconds", "mem_peak_mb", "rss_mb", "rss_delta_mb")}},
        "consistency": n1 == n2,
        "note": "stream 不构建全量 Python 列表；结果行数须与 baseline 一致。1000万/5000万行受本机1GB内存限制未测试。",
    }
    out = resolve("data/reports/stream_performance_v05.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": n, "baseline_peak_mb": report["baseline"]["mem_peak_mb"],
                      "stream_peak_mb": report["stream"]["mem_peak_mb"],
                      "consistent": report["consistency"], "report": str(out.relative_to(ROOT))},
                     ensure_ascii=False, indent=2))
    assert report["consistency"]


if __name__ == "__main__":
    main()
