"""v0.7.0 P8/P9：AI 假设引擎（结构化输出 + 去重）。

AI 只能提出假设；schema 校验、去重、字段/日期验证由确定性程序完成。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

REQUIRED = {"hypothesis_id", "market", "horizon", "target", "feature_ids",
            "expected_relation", "sample_period", "validation_plan",
            "falsification_criteria", "rationale"}
ALLOWED_MARKETS = {"CN_A", "US"}
ALLOWED_HORIZONS = {"1d", "5d", "20d"}


def validate_schema(h: dict[str, Any]) -> tuple[bool, str]:
    missing = REQUIRED - set(h)
    if missing:
        return False, f"missing:{sorted(missing)}"
    if h["market"] not in ALLOWED_MARKETS:
        return False, "invalid_market"
    if h["horizon"] not in ALLOWED_HORIZONS:
        return False, "invalid_horizon"
    sp = h["sample_period"]
    if not sp.get("start") or not sp.get("end") or sp["start"] > sp["end"]:
        return False, "invalid_sample_period"
    return True, ""


def hypothesis_fingerprint(h: dict[str, Any]) -> str:
    """数学等价去重：特征集合 + 目标 + 市场 + 周期 + 样本范围 + 关系方向。"""
    key = {
        "features": sorted(h["feature_ids"]), "target": h["target"],
        "market": h["market"], "horizon": h["horizon"],
        "relation": h["expected_relation"],
        "start": h["sample_period"]["start"], "end": h["sample_period"]["end"],
    }
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


class HypothesisEngine:
    def __init__(self, registry_path: str = "research_memory/hypotheses.json"):
        from pathlib import Path
        self.path = Path(registry_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = json.loads(self.path.read_text()) if self.path.exists() else []
        self.seen = {r["fingerprint"] for r in self.records}

    def submit(self, h: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        ok, reason = validate_schema(h)
        if not ok:
            return "REJECTED_SCHEMA", {"reason": reason}
        fp = hypothesis_fingerprint(h)
        if fp in self.seen:
            return "DUPLICATE", {"fingerprint": fp}
        rec = dict(h)
        rec["fingerprint"] = fp
        rec["research_family_id"] = "FAM_" + fp[:8]
        rec["status"] = "PROPOSED"
        rec["created_at"] = datetime.now(timezone.utc).isoformat()
        self.records.append(rec)
        self.seen.add(fp)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2))
        return "PROPOSED", rec
