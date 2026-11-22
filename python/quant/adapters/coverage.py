"""数据覆盖率报告：明确真实源缺口与 fixture 合成标注，训练前必须生成。"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from ..utils import ROOT, resolve, get_config
from ..logging_util import log


def build_coverage_report(raw_parquet: str | Path, out_path: str | Path | None = None) -> dict[str, Any]:
    p = resolve(str(raw_parquet))
    cfg = get_config()
    out = resolve(str(out_path or (cfg["storage"]["reports_dir"] + "/coverage_report.json")))
    out.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        report = {"ok": False, "reason": "no raw data", "generated_at": datetime.utcnow().isoformat()}
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    t = pq.read_table(str(p), columns=["symbol", "market", "domain", "available_time", "source"])
    syms = t.column("symbol").to_pylist()
    mkts = t.column("market").to_pylist()
    doms = t.column("domain").to_pylist()
    times = t.column("available_time").to_pylist()
    srcs = t.column("source").to_pylist()
    by_domain: dict[str, dict[str, Any]] = defaultdict(lambda: {"rows": 0, "min": None, "max": None})
    for d, tm in zip(doms, times):
        b = by_domain[d]
        b["rows"] += 1
        if tm:
            if b["min"] is None or tm < b["min"]:
                b["min"] = tm
            if b["max"] is None or tm > b["max"]:
                b["max"] = tm
    years = sorted({str(x)[:4] for x in times if x})
    synthetic = "fixture" in set(srcs)
    missing = []
    if synthetic:
        missing.append({"scope": "all", "reason": "synthetic fixture used; real source coverage not proven"})
    real_srcs = sorted({s for s in srcs if s != "fixture"})
    if not real_srcs:
        missing.append({"scope": "real_sources", "reason": "no live source rows available"})
    report = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat(),
        "file": str(p.relative_to(ROOT)),
        "total_rows": t.num_rows,
        "symbols": sorted(set(syms)),
        "markets": sorted(set(mkts)),
        "domains": dict(by_domain),
        "years_covered": years,
        "sources": sorted(set(srcs)),
        "synthetic": synthetic,
        "missing_ranges": missing,
        "training_ready": not synthetic or len(years) >= 2,
        "note": ("fixture 数据仅用于端到端验收；真实数据覆盖率需按数据源报告单独验证。"
                 if synthetic else "真实源覆盖率报告"),
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("覆盖率报告写入 %s (rows=%d, synthetic=%s)", out, t.num_rows, synthetic)
    return report
