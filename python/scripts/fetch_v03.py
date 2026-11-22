"""v0.3.0 P0 数据管道：扩展数据源 → 按市场/年/类别覆盖率报告 → raw_v03.parquet。

严格约束：
  * 真实数据 source=real:*、synthetic=false；缺口明确记录，不用合成数据补齐。
  * 宏观分两类：alfred_vintage(time_basis=real_published) 与 fred_v2(estimated)，严格模式只用前者。
  * 覆盖率矩阵：market × year × domain，含行数/标的数/synthetic 标记/缺口。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.adapters import (EastmoneyExpandAdapter, YahooUSAdapter, AlfredVintageAdapter,
                            AkshareExtendedAdapter, SECAdapterV3, CANONICAL_SCHEMA)
from quant.logging_util import log
from quant.storage import MetadataDB
from quant.utils import ROOT, measure, resolve

ADAPTERS = {
    "eastmoney_expand": EastmoneyExpandAdapter,
    "yahoo_us": YahooUSAdapter,
    "alfred_vintage": AlfredVintageAdapter,
    "akshare_extended": AkshareExtendedAdapter,
    "sec_v3": SECAdapterV3,
}
DEFAULT = "eastmoney_expand,yahoo_us,alfred_vintage,akshare_extended,sec_v3"


def coverage_matrix(tables: dict[str, pa.Table], results: dict[str, Any]) -> dict[str, Any]:
    """每个市场、每年、每个数据类别的实际覆盖率。"""
    cells: dict[tuple, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "symbols": set(), "sources": set()})
    total = 0
    synthetic_rows = 0
    for name, t in tables.items():
        cols = {c: t.column(c).to_pylist() for c in
                ("symbol", "market", "domain", "available_time", "source")}
        for i in range(t.num_rows):
            m = cols["market"][i] or "UNKNOWN"
            d = cols["domain"][i] or "unknown"
            tm = cols["available_time"][i]
            y = str(tm)[:4] if tm else "unknown"
            cell = cells[(m, y, d)]
            cell["rows"] += 1
            cell["symbols"].add(cols["symbol"][i])
            cell["sources"].add(cols["source"][i])
            total += 1
            if "fixture" in str(cols["source"][i]):
                synthetic_rows += 1
    matrix = []
    for (m, y, d), v in sorted(cells.items()):
        matrix.append({"market": m, "year": y, "domain": d,
                       "rows": v["rows"], "symbols": len(v["symbols"]),
                       "sources": sorted(v["sources"])})
    missing = []
    for src, res in results.items():
        for g in res.get("missing_ranges", []):
            missing.append({"source": src, **g})
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "version": "v0.3.0", "data_origin": "real", "synthetic": synthetic_rows > 0,
        "total_rows": total, "synthetic_rows": synthetic_rows,
        "matrix": matrix, "missing_ranges": missing,
        "sources": list(tables.keys()),
        "time_basis_note": "macro_vintage 使用 ALFRED 真实版本(real_published)；fred_v2 为 estimated 严格模式禁用",
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default=DEFAULT)
    args = p.parse_args()
    meta = MetadataDB()
    with measure("v03_data_fetch") as st:
        tables: dict[str, pa.Table] = {}
        results: dict[str, Any] = {}
        for name in args.sources.split(","):
            name = name.strip()
            if name not in ADAPTERS:
                log.warning("未知源 %s", name); continue
            try:
                res = ADAPTERS[name]().fetch()
                tables[name] = res.table
                results[name] = {"rows": res.table.num_rows,
                                 "coverage": res.coverage,
                                 "missing_ranges": res.missing_ranges}
                rid = f"v03-{name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                meta.execute(
                    "INSERT OR REPLACE INTO source_runs VALUES (?,?,?,?,?,?,?,?)",
                    (rid, name, "live", res.started_at, datetime.utcnow().isoformat(),
                     res.table.num_rows, "ok",
                     json.dumps({"version": "v0.3.0", **res.coverage}, ensure_ascii=False)),
                )
                log.info("v0.3 源 %s: %d 行", name, res.table.num_rows)
            except Exception as e:
                log.exception("v0.3 源 %s 失败", name)
                results[name] = {"error": str(e), "missing_ranges": [{"reason": str(e)}]}
        if not tables:
            raise SystemExit("无可用真实源")
        combined = pa.concat_tables(list(tables.values()), promote_options="default")
        combined = combined.select([f for f in CANONICAL_SCHEMA.names
                                    if f in combined.column_names]).cast(CANONICAL_SCHEMA)
        raw_path = resolve("data/raw/raw_v03.parquet")
        pq.write_table(combined, str(raw_path), compression="zstd")
        cov = coverage_matrix(tables, results)
        cov_path = resolve("data/reports/coverage_report_v03.json")
        cov_path.parent.mkdir(parents=True, exist_ok=True)
        cov_path.write_text(json.dumps(cov, ensure_ascii=False, indent=2), encoding="utf-8")
        disk = raw_path.stat().st_size
    meta.save_metric(st, disk)
    meta.close()
    print(json.dumps({"raw": str(raw_path.relative_to(ROOT)), "rows": combined.num_rows,
                      "sources": {k: v.get("rows", v.get("error")) for k, v in results.items()},
                      "coverage": str(cov_path.relative_to(ROOT)),
                      "synthetic": cov["synthetic"], "perf": st},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
