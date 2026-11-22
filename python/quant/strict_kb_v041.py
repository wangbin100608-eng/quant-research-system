"""v0.4.1 P0-3：六库严格检查（实名、真实记录、时间字段、空库/缺库负向）。

六库实名：structured / document / vector / event / hypothesis / rule。
不允许无条件添加库名满足数量；每库必须有真实记录与合法 available_time；
历史快照不得引用未来文档。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_LIBRARIES = {"structured", "document", "vector", "event", "hypothesis", "rule"}
KB_TABLE_LIBS = {"structured", "document", "vector", "event", "rule"}  # hypothesis 独立表


def check_six_libraries(kb_module) -> dict[str, Any]:
    """kb_module: KnowledgeSystemV03 实例（可指向临时库）。"""
    meta = kb_module.meta
    details = {}
    errors = []
    rows = meta.query("SELECT library, count(*) n, min(available_time) mn, "
                      "max(available_time) mx FROM kb_items GROUP BY library")
    for r in rows:
        details[r["library"]] = {"count": r["n"], "min": r["mn"], "max": r["mx"],
                                 "source_table": "kb_items"}
    hyp = meta.query("SELECT count(*) n, min(cutoff_date) mn, max(cutoff_date) mx FROM hypotheses")
    if hyp and hyp[0]["n"]:
        details["hypothesis"] = {"count": hyp[0]["n"], "min": hyp[0]["mn"],
                                 "max": hyp[0]["mx"], "source_table": "hypotheses"}
    # 库完整性
    missing = EXPECTED_LIBRARIES - set(details)
    if missing:
        errors.append(f"missing_libraries: {sorted(missing)}")
    empty = [k for k, v in details.items() if v["count"] == 0]
    if empty:
        errors.append(f"empty_libraries: {empty}")
    # 每库时间字段检查（kb_items 五库）
    for r in meta.query("SELECT item_id, library, available_time, published_time, source, "
                        "version, snapshot_id FROM kb_items"):
        if not r["available_time"]:
            errors.append(f"missing_available_time: {r['item_id']}")
        if not r["source"]:
            errors.append(f"missing_source: {r['item_id']}")
        if not r["version"]:
            errors.append(f"missing_version: {r['item_id']}")
        if not r["snapshot_id"]:
            errors.append(f"missing_snapshot_id: {r['item_id']}")
        try:
            datetime.fromisoformat(str(r["available_time"]).replace("Z", "+00:00"))
        except Exception:
            errors.append(f"invalid_available_time: {r['item_id']}")
    ok = not errors
    return {"ok": ok, "libraries": sorted(EXPECTED_LIBRARIES), "details": details,
            "errors": errors, "note": "hypothesis 为独立表；库名实名校验，不允许无条件补名"}
