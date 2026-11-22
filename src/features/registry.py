"""v0.7.0 P5：Feature Factory + Registry（复用现有 features/horizons）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant.models import FEATURE_NUMERIC
from quant.features import FEATURE_VERSION


@dataclass(frozen=True)
class FeatureSpec:
    feature_id: str
    feature_name: str
    category: str
    description: str
    input_columns: tuple[str, ...]
    parameters: dict[str, Any]
    lookback_window: int
    output_type: str
    availability_rule: str
    missing_policy: str
    version: str
    code_hash: str
    created_at: str


class FeatureRegistry:
    def __init__(self, path: str | Path = "data/metadata/feature_registry.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.specs: dict[str, FeatureSpec] = {}
        if self.path.exists():
            for d in json.loads(self.path.read_text()).get("features", []):
                self.specs[d["feature_id"]] = FeatureSpec(**d)
        self._bootstrap()

    def _bootstrap(self):
        """注册现有 6 个数值特征（P0 审计结果）。"""
        mapping = {
            "trailing_mean_return_1y": ("price", 252),
            "trailing_mean_return_2y": ("price", 504),
            "trailing_vol_1y": ("price", 252),
            "momentum_6m": ("price", 126),
            "avg_turnover_log": ("volume", 252),
            "eps_latest": ("fundamental_proxy", 0),
        }
        code_hash = hashlib.sha256("quant.features.v1".encode()).hexdigest()
        for name in FEATURE_NUMERIC:
            fid = f"FEAT_{name.upper()}"
            if fid not in self.specs:
                cat, lookback = mapping[name]
                self.specs[fid] = FeatureSpec(
                    feature_id=fid, feature_name=name, category=cat,
                    description="v0.6 baseline feature",
                    input_columns=("close", "volume"),
                    parameters={}, lookback_window=lookback,
                    output_type="float", availability_rule="available_at<=cutoff",
                    missing_policy="reject_in_strict", version=FEATURE_VERSION,
                    code_hash=code_hash,
                    created_at=datetime.now(timezone.utc).isoformat())

    def register(self, spec: FeatureSpec) -> str:
        self.specs[spec.feature_id] = spec
        return spec.feature_id

    def get(self, feature_id: str) -> FeatureSpec:
        return self.specs[feature_id]

    def save(self):
        out = {"version": "v0.7.0", "features": [asdict(s) for s in self.specs.values()]}
        self.path.write_text(json.dumps(out, ensure_ascii=False, indent=2,
                                        default=list), encoding="utf-8")
        return self.path
