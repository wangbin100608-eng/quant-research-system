"""v0.3.0 P2：性能报告（44k 真实数据）——运行时间/RSS/磁盘/结果一致性。

保持 Rust+Python 混合架构：Rust 时间隔离/哈希；Polars+DuckDB 分析；Python 模型。
禁止不必要复制：全部通过列投影/惰性扫描/分区读取。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

from quant.utils import ROOT, measure, resolve


def bench(label, fn, repeats=5):
    import statistics
    samples, result = [], None
    for _ in range(repeats):
        with measure(label) as st:
            result = fn()
        samples.append({k: st[k] for k in ("elapsed_seconds", "mem_peak_mb",
                                            "rss_mb", "rss_delta_mb")})
    return {
        "task": label,
        "elapsed_seconds": round(statistics.median(s["elapsed_seconds"] for s in samples), 4),
        "mem_peak_mb": round(statistics.median(s["mem_peak_mb"] for s in samples), 2),
        "rss_mb": round(statistics.median(s["rss_mb"] for s in samples), 2),
        "rss_delta_mb": round(statistics.median(s["rss_delta_mb"] for s in samples), 2),
        "result": result, "repeats": repeats,
    }


def main():
    raw = resolve("data/raw/raw_v03.parquet")
    if not raw.exists():
        raise SystemExit("先运行 fetch_v03.py")
    disk_mb = round(raw.stat().st_size / 1024 / 1024, 2)
    results = {}

    def read_all():
        return pq.read_table(str(raw)).num_rows
    results["baseline_read_all"] = bench("baseline_read_all", read_all)

    def read_projected():
        return pq.read_table(str(raw), columns=["symbol", "market", "domain",
                                                "available_time"]).num_rows
    results["optimized_column_projection"] = bench("optimized_column_projection", read_projected)

    def polars_lazy():
        import polars as pl
        return (pl.scan_parquet(str(raw))
                .select(["symbol", "market", "domain", "available_time"])
                .filter(pl.col("market") == "US").collect().height)
    results["optimized_polars_lazy"] = bench("optimized_polars_lazy", polars_lazy)

    def duckdb_agg():
        import duckdb
        con = duckdb.connect()
        r = con.execute(f"SELECT market, substr(available_time,1,4), count(*) "
                        f"FROM read_parquet('{raw}') GROUP BY 1,2").fetchall()
        con.close()
        return {(m, y): n for m, y, n in r}
    results["optimized_duckdb_agg"] = bench("optimized_duckdb_agg", duckdb_agg)

    def python_agg():
        from collections import Counter
        t = pq.read_table(str(raw), columns=["market", "available_time"])
        return dict(Counter((m, str(v)[:4]) for m, v in
                            zip(t.column("market").to_pylist(),
                                t.column("available_time").to_pylist())))
    results["baseline_python_agg"] = bench("baseline_python_agg", python_agg)

    def rust_hash():
        import subprocess
        p = ROOT / "rust_core/target/release/timeguard"
        if not p.exists():
            return "unavailable"
        out = subprocess.run([str(p), "hash", "--file", str(raw)],
                             capture_output=True, text=True).stdout.strip()
        return out
    results["rust_hash"] = bench("rust_hash", rust_hash, repeats=3)

    import hashlib
    py_hash = hashlib.sha256(raw.read_bytes()).hexdigest()
    rh = results["rust_hash"]["result"]
    consistency = {
        "rust_hash_equals_python": isinstance(rh, str) and rh == py_hash,
        "duckdb_equals_python_agg": results["duckdb_agg_result_consistent"] if "duckdb_agg_result_consistent" in results
                                    else (results["optimized_duckdb_agg"]["result"] == results["baseline_python_agg"]["result"]),
        "projection_rows_equal": (results["optimized_column_projection"]["result"]
                                  == results["baseline_read_all"]["result"]),
    }
    # 序列化结果
    for r in results.values():
        if isinstance(r.get("result"), dict) and any(isinstance(k, tuple) for k in r["result"]):
            r["result"] = {f"{k[0]}|{k[1]}": v for k, v in sorted(r["result"].items())}
    sp = lambda b, o: round(results[b]["elapsed_seconds"] / results[o]["elapsed_seconds"], 2)
    report = {
        "generated_at": datetime.utcnow().isoformat(), "version": "v0.3.0",
        "data_source": "real raw_v03.parquet", "rows": results["baseline_read_all"]["result"],
        "disk_mb": disk_mb,
        "architecture": {"rust": "time isolation + hash + audit (unchanged)",
                         "polars_duckdb": "columnar analysis (no data copies)",
                         "python": "model training"},
        "results": results,
        "consistency": consistency,
        "summary": {
            "column_projection_speedup": sp("baseline_read_all", "optimized_column_projection"),
            "duckdb_vs_python_agg": sp("baseline_python_agg", "optimized_duckdb_agg"),
            "polars_lazy_scan_note": "列投影+过滤，无中间副本",
        },
    }
    out = resolve("data/reports/performance_report_v03.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"rows": report["rows"], "disk_mb": disk_mb,
                      "consistency": consistency, "summary": report["summary"],
                      "report": str(out)}, ensure_ascii=False, indent=2))
    assert consistency["rust_hash_equals_python"]


if __name__ == "__main__":
    main()
