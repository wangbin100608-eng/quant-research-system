"""真实数据管道 v0.2.0：抓取真实源 → 分市场年份覆盖率报告 → 写 raw_real.parquet。

关键约束：
  * 真实数据写 data/raw/raw_real.parquet，fixture 写 raw_fixture.parquet，绝不混写。
  * 覆盖率报告按 (market, year, domain) 统计实际行数与缺口，明确 synthetic=false。
  * 每个源独立适配器，失败记录缺口不伪造。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.adapters import (EastmoneyCNAdapter, YahooUSAdapter,
                            FREDAdapterV2, SECAdapterV2, NewsAdapterV2,
                            CANONICAL_SCHEMA)
from quant.logging_util import log
from quant.storage import MetadataDB
from quant.utils import ROOT, measure, resolve

ADAPTERS = {
    "eastmoney_cn": EastmoneyCNAdapter,
    "yahoo_us": YahooUSAdapter,
    "fred_v2": FREDAdapterV2,
    "sec_v2": SECAdapterV2,
    "news_v2": NewsAdapterV2,
}


def real_coverage_report(tables: dict[str, pa.Table], results: dict[str, Any]) -> dict[str, Any]:
    """按市场/年份/域统计真实覆盖率。"""
    by_cell: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "symbols": set()})
    total = 0
    for name, t in tables.items():
        syms = t.column("symbol").to_pylist()
        mkts = t.column("market").to_pylist()
        doms = t.column("domain").to_pylist()
        times = t.column("available_time").to_pylist()
        for s, m, dom, tm in zip(syms, mkts, doms, times):
            y = str(tm)[:4] if tm else "unknown"
            cell = by_cell[(m, y, dom)]
            cell["rows"] += 1
            cell["symbols"].add(s)
            total += 1
    cells = []
    for (m, y, dom), v in sorted(by_cell.items()):
        cells.append({"market": m, "year": y, "domain": dom,
                      "rows": v["rows"], "symbols": len(v["symbols"])})
    missing = []
    for name, res in results.items():
        for g in res.get("missing_ranges", []):
            missing.append({"source": name, **g})
    report = {
        "data_origin": "real", "synthetic": False,
        "generated_at": datetime.utcnow().isoformat(),
        "total_rows": total,
        "by_market_year_domain": cells,
        "missing_ranges": missing,
        "sources": list(tables.keys()),
        "note": "真实公开数据源覆盖率；缺失范围明确列出，未用合成数据补齐。",
    }
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default="eastmoney_cn,yahoo_us,fred_v2,sec_v2")
    args = p.parse_args()
    meta = MetadataDB()
    with measure("real_data_fetch") as st:
        tables: dict[str, pa.Table] = {}
        results: dict[str, Any] = {}
        for name in args.sources.split(","):
            name = name.strip()
            if name not in ADAPTERS:
                log.warning("未知源 %s 跳过", name)
                continue
            try:
                res = ADAPTERS[name]().fetch()
                tables[name] = res.table
                results[name] = {
                    "rows": res.table.num_rows,
                    "coverage": res.coverage,
                    "missing_ranges": res.missing_ranges,
                }
                rid = f"real-{name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                meta.execute(
                    "INSERT OR REPLACE INTO source_runs VALUES (?,?,?,?,?,?,?,?)",
                    (rid, name, "live", res.started_at, datetime.utcnow().isoformat(),
                     res.table.num_rows, "ok",
                     json.dumps({"data_origin": "real", **res.coverage}, ensure_ascii=False)),
                )
                log.info("真实源 %s: %d 行", name, res.table.num_rows)
            except Exception as e:
                log.exception("真实源 %s 失败", name)
                results[name] = {"error": str(e), "missing_ranges": [{"reason": str(e)}]}
        if not tables:
            raise SystemExit("无可用真实源")
        combined = pa.concat_tables(list(tables.values()), promote_options="default")
        combined = combined.select([f for f in CANONICAL_SCHEMA.names
                                    if f in combined.column_names]).cast(CANONICAL_SCHEMA)
        raw_path = resolve("data/raw/raw_real.parquet")
        pq.write_table(combined, str(raw_path), compression="zstd")
        cov = real_coverage_report(tables, results)
        cov_path = resolve("data/reports/coverage_report_real.json")
        cov_path.parent.mkdir(parents=True, exist_ok=True)
        cov_path.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
        disk = raw_path.stat().st_size
    meta.save_metric(st, disk)
    meta.close()
    print(json.dumps({
        "raw_real": str(raw_path.relative_to(ROOT)), "rows": combined.num_rows,
        "sources": {k: v.get("rows", v.get("error")) for k, v in results.items()},
        "coverage_report": str(cov_path.relative_to(ROOT)),
        "perf": st,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
