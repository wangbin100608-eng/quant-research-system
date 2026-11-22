"""性能与内存优化 v0.2.0：可重复的改动前后对比基准。

对比维度（用户要求）：加载速度、内存占用、查询耗时、结果一致性。
优化点：
  1. 列投影 vs 全列加载（加载速度 + 内存）
  2. Parquet 按 market/year 分区写入 vs 单文件
  3. Polars 惰性扫描 vs Pandas 式全量循环（查询耗时）
  4. DuckDB 列式聚合 vs Python 循环（查询耗时）
  5. 去重加载：Polars/DuckDB 去重 vs Python set 去重（速度 + 一致性）
  6. 结果一致性：Python / DuckDB / Polars 三种聚合结果必须完全相等；两种去重行数必须相等

数据口径、目录结构、模型基准流程保持不变；仅读 raw_real.parquet，写 data/clean 与 data/reports。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quant.utils import ROOT, measure, resolve


def bench(label, fn, repeats=5):
    """多轮重复取中位数，避免冷启动/抖动；结果取最后一轮。"""
    import statistics
    samples = []
    result = None
    for _ in range(repeats):
        with measure(label) as st:
            result = fn()
        samples.append({k: st[k] for k in
                        ("elapsed_seconds", "mem_peak_mb", "rss_mb", "rss_delta_mb")})
    med = {
        "elapsed_seconds": round(statistics.median(s["elapsed_seconds"] for s in samples), 4),
        "mem_peak_mb": round(statistics.median(s["mem_peak_mb"] for s in samples), 2),
        "rss_mb": round(statistics.median(s["rss_mb"] for s in samples), 2),
        "rss_delta_mb": round(statistics.median(s["rss_delta_mb"] for s in samples), 2),
    }
    return {"task": label, **med, "result": result,
            "repeats": repeats,
            "elapsed_all_runs": [s["elapsed_seconds"] for s in samples]}


def key(market, avert):
    return (market, str(avert)[:4] if avert else "unknown")


def main():
    raw = resolve("data/raw/raw_real.parquet")
    if not raw.exists():
        raise SystemExit("先运行 fetch_real.py 生成 raw_real.parquet")
    results = {}

    # ---------- 加载速度 / 内存 ----------
    def read_all():
        t = pq.read_table(str(raw))
        return t.num_rows
    results["baseline_read_all_cols"] = bench("baseline_read_all_cols", read_all)

    def read_projected():
        t = pq.read_table(str(raw), columns=["symbol", "market", "available_time", "domain"])
        return t.num_rows
    results["optimized_column_projection"] = bench("optimized_column_projection", read_projected)

    # ---------- Parquet 分区写入 ----------
    t = pq.read_table(str(raw), columns=["symbol", "market", "domain", "available_time", "payload"])
    years = [str(v)[:4] if v else "unknown" for v in t.column("available_time").to_pylist()]
    table = t.append_column("year", pa.array(years, type=pa.string()))
    part_dir = resolve("data/clean/raw_real_partitioned")
    part_dir.parent.mkdir(parents=True, exist_ok=True)

    def write_partition():
        pq.write_to_dataset(table, root_path=str(part_dir),
                            partition_cols=["market", "year"], compression="zstd")
        return sum(1 for _ in part_dir.rglob("*.parquet"))
    results["optimized_parquet_partition_write"] = bench(
        "optimized_parquet_partition_write", write_partition)

    # ---------- 查询耗时 + 结果一致性（聚合）----------
    def python_agg():
        tt = pq.read_table(str(raw), columns=["market", "available_time"])
        from collections import Counter
        c = Counter(key(m, v) for m, v in zip(tt.column("market").to_pylist(),
                                              tt.column("available_time").to_pylist()))
        return dict(c)
    results["baseline_python_aggregation"] = bench("baseline_python_aggregation", python_agg)

    def duckdb_agg():
        import duckdb
        con = duckdb.connect()
        rows = con.execute(f"""
            SELECT market, substr(available_time,1,4) AS y, count(*) AS n
            FROM read_parquet('{raw}') GROUP BY 1,2
        """).fetchall()
        con.close()
        return {(m, y): n for m, y, n in rows}
    results["optimized_duckdb_aggregation"] = bench("optimized_duckdb_aggregation", duckdb_agg)

    def polars_agg():
        import polars as pl
        lf = pl.scan_parquet(str(raw)).select(["market", "available_time"])
        df = lf.group_by(["market", pl.col("available_time").str.slice(0, 4).alias("y")]).len()
        out = df.collect()
        return {(m, y): n for m, y, n in out.iter_rows()}
    results["optimized_polars_aggregation"] = bench("optimized_polars_aggregation", polars_agg)

    # ---------- 去重加载 + 结果一致性 ----------
    def python_dedup():
        tt = pq.read_table(str(raw), columns=["symbol", "available_time", "domain", "source"])
        rows = list(zip(tt.column("symbol").to_pylist(),
                        tt.column("available_time").to_pylist(),
                        tt.column("domain").to_pylist(),
                        tt.column("source").to_pylist()))
        return len(set(rows))
    results["baseline_python_dedup"] = bench("baseline_python_dedup", python_dedup)

    def polars_dedup():
        import polars as pl
        return pl.scan_parquet(str(raw)).select(
            ["symbol", "available_time", "domain", "source"]).unique().collect().height
    results["optimized_polars_dedup"] = bench("optimized_polars_dedup", polars_dedup)

    def duckdb_dedup():
        import duckdb
        con = duckdb.connect()
        n = con.execute(f"""SELECT count(*) FROM (
            SELECT DISTINCT symbol, available_time, domain, source FROM read_parquet('{raw}'))""").fetchone()[0]
        con.close()
        return n
    results["optimized_duckdb_dedup"] = bench("optimized_duckdb_dedup", duckdb_dedup)

    # ---------- 一致性断言 ----------
    agg_py = results["baseline_python_aggregation"]["result"]
    agg_dd = results["optimized_duckdb_aggregation"]["result"]
    agg_pl = results["optimized_polars_aggregation"]["result"]
    dedup_py = results["baseline_python_dedup"]["result"]
    dedup_pl = results["optimized_polars_dedup"]["result"]
    dedup_dd = results["optimized_duckdb_dedup"]["result"]
    consistency = {
        "aggregation_python_equals_duckdb": agg_py == agg_dd,
        "aggregation_python_equals_polars": agg_py == agg_pl,
        "dedup_python_equals_polars": dedup_py == dedup_pl,
        "dedup_python_equals_duckdb": dedup_py == dedup_dd,
        "aggregation_cells": len(agg_py),
        "dedup_rows": dedup_py,
    }
    if agg_py != agg_dd:
        consistency["agg_py_only"] = {str(k): agg_py[k] for k in set(agg_py) - set(agg_dd) if agg_py[k] != agg_dd.get(k)}
        consistency["agg_dd_only"] = {str(k): agg_dd[k] for k in set(agg_dd) - set(agg_py) if agg_dd[k] != agg_py.get(k)}
    consistency["all_consistent"] = all(
        v for k, v in consistency.items() if isinstance(v, bool))

    # ---------- 加速比 ----------
    def sp(base, opt):
        b = results[base]["elapsed_seconds"]; o = results[opt]["elapsed_seconds"]
        return round(b / o, 2) if o > 0 else None
    summary = {
        "column_projection_load_speedup": sp("baseline_read_all_cols", "optimized_column_projection"),
        "duckdb_vs_python_agg_speedup": sp("baseline_python_aggregation", "optimized_duckdb_aggregation"),
        "polars_vs_python_agg_speedup": sp("baseline_python_aggregation", "optimized_polars_aggregation"),
        "polars_dedup_vs_python_speedup": sp("baseline_python_dedup", "optimized_polars_dedup"),
        "duckdb_dedup_vs_python_speedup": sp("baseline_python_dedup", "optimized_duckdb_dedup"),
        "partition_files": results["optimized_parquet_partition_write"]["result"],
        "all_consistent": consistency["all_consistent"],
    }
    # 可视化结果存 JSON（聚合结果可能含 tuple key，转字符串）
    agg_serializable = {f"{m}|{y}": n for (m, y), n in sorted(agg_py.items())}
    # results 内嵌的 result 也可能含 tuple key，序列化前统一转换
    for r in results.values():
        if isinstance(r.get("result"), dict) and any(isinstance(k, tuple) for k in r["result"]):
            r["result"] = {f"{k[0]}|{k[1]}": v for k, v in sorted(r["result"].items())}
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "data_source": "real (raw_real.parquet)",
        "rows": results["baseline_read_all_cols"]["result"],
        "memory_metric_fix": "tracemalloc 嵌套引用计数 + ru_maxrss 进程 RSS 峰值",
        "optimized": {
            "column_projection": True,
            "parquet_partition": f"data/clean/raw_real_partitioned (market/year, {summary['partition_files']} files)",
            "polars_lazy_scan": True,
            "duckdb_columnar_aggregation": True,
            "deduplicated_loading": True,
        },
        "results": results,
        "consistency": consistency,
        "aggregation_result": agg_serializable,
        "summary": summary,
    }
    out = resolve("data/reports/performance_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "summary": summary,
        "consistency": consistency,
        "mem_peaks_mb": {k: v["mem_peak_mb"] for k, v in results.items()},
        "elapsed_s": {k: v["elapsed_seconds"] for k, v in results.items()},
        "report": str(out),
    }, ensure_ascii=False, indent=2))
    assert consistency["all_consistent"], "结果一致性检查失败"


if __name__ == "__main__":
    main()
