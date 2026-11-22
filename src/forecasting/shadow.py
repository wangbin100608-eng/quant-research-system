"""v0.7.0 P16/P17：市场×周期模型注册 + Shadow Mode（只预测冻结，不交易）。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sklearn.linear_model import LinearRegression, Ridge, ElasticNet
from quant.models import TRAINERS

GROUPS = [(m, h) for m in ("CN_A", "US") for h in ("1d", "5d", "20d")]


def build_model_groups() -> dict[str, list[str]]:
    """六组模型（A/US × 1d/5d/20d），复用现有 6 模型 + 新增线性族。"""
    base = ["naive_last", "historical_mean", "linear", "ridge", "elasticnet",
            "lightgbm", "catboost", "xgboost", "lightgbm_ranker"]
    return {f"{m}_{h}": base for m, h in GROUPS}


class ShadowMode:
    def __init__(self, path: str = "data/metadata/shadow_predictions.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = json.loads(self.path.read_text()) if self.path.exists() else []

    def freeze_prediction(self, market: str, horizon: str, prediction_time: str,
                          symbol: str, predicted_return: float,
                          predicted_rank: int | None = None,
                          confidence_interval: tuple[float, float] | None = None,
                          uncertainty: float | None = None,
                          model_version: str = "unknown",
                          feature_version: str = "unknown",
                          data_manifest_hash: str = "unknown") -> dict[str, Any]:
        rec = {
            "prediction_id": "PRED_" + hashlib.sha256(
                f"{market}|{horizon}|{prediction_time}|{symbol}|{model_version}".encode()
            ).hexdigest()[:16],
            "market": market, "horizon": horizon, "symbol": symbol,
            "prediction_time": prediction_time,
            "predicted_return": float(predicted_return),
            "predicted_rank": predicted_rank,
            "confidence_interval": list(confidence_interval) if confidence_interval else None,
            "uncertainty": uncertainty,
            "model_version": model_version, "feature_version": feature_version,
            "data_manifest_hash": data_manifest_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "shadow_only": True, "real_orders": False,
        }
        rec["record_sha256"] = hashlib.sha256(
            json.dumps(rec, sort_keys=True).encode()).hexdigest()
        self.records.append(rec)
        self.path.write_text(json.dumps(self.records, ensure_ascii=False, indent=2))
        return rec

    def evaluate_mature(self, prediction_id: str, actual_return: float) -> dict[str, Any] | None:
        """标签成熟后评估（本方法不修改冻结预测，只返回评估结果）。"""
        rec = next((r for r in self.records if r["prediction_id"] == prediction_id), None)
        if rec is None:
            return None
        err = float(actual_return) - rec["predicted_return"]
        return {"prediction_id": prediction_id, "actual_return": float(actual_return),
                "error": err, "abs_error": abs(err),
                "horizon": rec["horizon"], "evaluated_at": datetime.now(timezone.utc).isoformat()}
