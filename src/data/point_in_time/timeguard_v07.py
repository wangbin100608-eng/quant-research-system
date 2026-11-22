"""v0.7.0 P4：TimeGuard 统一门面（复用 v0.4-v0.6 实现）。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
from quant.timestamps_v04 import to_utc, cutoff_for_year, FIELD_AVAILABLE


class TimeGuardV07:
    def __init__(self, strict: bool = True):
        self.reader = TimeGuardReader(strict=strict)
        self.strict = strict

    @staticmethod
    def _prediction_time(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, int):
            return cutoff_for_year(value + 1)
        return to_utc(value)

    def validate_record(self, record: dict[str, Any], prediction_time) -> bool:
        cutoff = self._prediction_time(prediction_time)
        return self.reader.validate_row(record, cutoff)

    def filter_available_data(self, dataset, prediction_time, **kwargs):
        """dataset 为 parquet 路径或行列表。"""
        cutoff = self._prediction_time(prediction_time)
        if isinstance(dataset, (str, __import__("pathlib").Path)):
            return self.reader.read_parquet(dataset, cutoff, **kwargs)
        return self.reader.filter_rows(dataset, cutoff), {"engine": "python_rows"}

    def validate_feature(self, feature: dict[str, Any], prediction_time) -> bool:
        # 特征必须携带 available_at 且不晚于 cutoff
        return self.validate_record(feature, prediction_time)

    def validate_model_provenance(self, model: dict[str, Any], prediction_time) -> bool:
        cutoff = self._prediction_time(prediction_time)
        av = model.get("train_max_available_at") or model.get("trained_to")
        dt = to_utc(av)
        if dt is None:
            if self.strict:
                raise TimeGuardReadError("model provenance missing available time")
            return True
        if dt > cutoff:
            raise TimeGuardReadError(f"future model provenance: {av} > {cutoff.isoformat()}")
        return True
