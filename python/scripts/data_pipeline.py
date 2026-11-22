"""数据管道 CLI：抓取 -> 清洗 -> Parquet -> 覆盖率报告。"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.adapters import (FixtureAdapter, FredAdapter, SecEdgarAdapter,
                            AkshareCNAdapter, build_coverage_report, CANONICAL_SCHEMA)
from quant.logging_util import log
from quant.storage import MetadataDB
from quant.utils import ROOT, get_config, measure, resolve


def adapter_from_name(name: str, **kw):
    return {
        "fixture": FixtureAdapter,
        "fred": FredAdapter,
        "sec": SecEdgarAdapter,
        "sec_edgar": SecEdgarAdapter,
        "akshare": AkshareCNAdapter,
        "akshare_cn": AkshareCNAdapter,
    }[name](**kw)


def cmd_fetch(args):
    cfg = get_config()
    meta = MetadataDB()
    with measure("data_fetch") as st:
        all_tables = []
        statuses = []
        for name in args.sources.split(","):
            name = name.strip()
            if not name:
                continue
            try:
                ad = adapter_from_name(name)
                res = ad.fetch()
                all_tables.append(res.table)
                rid = f"{name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
                meta.execute(
                    "INSERT OR REPLACE INTO source_runs VALUES (?,?,?,?,?,?,?,?)",
                    (rid, name, res.mode, res.started_at,
                     datetime.utcnow().isoformat(), res.table.num_rows,
                     "ok", json.dumps(res.coverage, ensure_ascii=False)),
                )
                statuses.append({"source": name, "mode": res.mode,
                                 "rows": res.table.num_rows,
                                 "missing": res.missing_ranges[:3]})
                log.info("适配器 %s 模式=%s 行数=%d", name, res.mode, res.table.num_rows)
            except Exception as e:
                log.exception("适配器 %s 失败", name)
                statuses.append({"source": name, "error": str(e)})
        if not all_tables:
            raise SystemExit("没有可用数据源")
        table = pa.concat_tables(all_tables, promote=True)
        # 统一 schema
        table = table.select([f for f in CANONICAL_SCHEMA.names if f in table.column_names])
        table = table.cast(CANONICAL_SCHEMA)
        raw_path = resolve(cfg["storage"]["raw_dir"] + "/raw_all.parquet")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(raw_path), compression="zstd")
        cov = build_coverage_report(raw_path)
        disk = raw_path.stat().st_size
    meta.save_metric(st, disk)
    meta.close()
    out = {
        "raw_path": str(raw_path.relative_to(ROOT)),
        "rows": table.num_rows,
        "sources": statuses,
        "coverage": cov,
        "perf": st,
        "sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def main():
    p = argparse.ArgumentParser(prog="quant-data")
    sub = p.add_subparsers(dest="cmd")
    f = sub.add_parser("fetch")
    f.add_argument("--sources", default="fixture")
    f.add_argument("--coverage", action="store_true")
    args = p.parse_args()
    if args.cmd == "fetch":
        cmd_fetch(args)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
