"""v0.7.0 P18：研究 Dashboard 数据聚合（数据源/特征/假设/实验/预测五页数据）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _load_json(rel: str, default=None):
    p = ROOT / rel
    return json.loads(p.read_text()) if p.exists() else (default or {})


def build_research_dashboard(path: str = "web/research_dashboard.json") -> dict[str, Any]:
    cfg = yaml.safe_load((ROOT / "config/system.yaml").read_text())
    sources = yaml.safe_load((ROOT / "config/data_sources.yaml").read_text())
    reports = {
        "data_page": _load_json("data/reports/coverage_report_v03.json", {}),
        "feature_page": {"registry": "data/metadata/feature_registry.json",
                         "baseline_features": 6, "categories": ["price", "volume", "fundamental_proxy"]},
        "hypothesis_page": _load_json("research_memory/hypotheses.json", []),
        "experiment_page": {"lab": "src/experiments/lab.py", "tables": ["experiments", "research_conclusions"]},
        "prediction_page": _load_json("data/metadata/shadow_predictions.json", []),
    }
    states = ["PROPOSED", "REJECTED_SCHEMA", "DUPLICATE", "RUNNING", "NO_EVIDENCE",
              "PROMISING", "VALIDATED", "DEGRADED", "RETIRED"]
    out = {
        "version": cfg["version"], "generated_at": datetime.now(timezone.utc).isoformat(),
        "status_enum": states,
        "data_sources": sources["providers"],
        "pages": reports,
        "note": "VALIDATED 仅表示指定条件下的统计检验通过，不代表未来一定有效",
    }
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
