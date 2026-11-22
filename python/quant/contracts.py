"""统一数据契约：所有数据文件、知识条目、模型、预测都必须遵守此模块定义。

五时间字段：
  event_time      事件实际发生时间
  published_time  信息公开披露时间
  available_time  研究者在某个时点真正可获得的时间（准入主字段）
  collected_time  系统抓取时间
  revision_time   数据修订时间（ALFRED vintage）

准入判断以 available_time <= cutoff_date 为准。
若 available_time 为 None 且 strict_point_in_time 开启，则视为 unknown_availability 并拒绝。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional


class Availability(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown_availability"


class DataDomain(str, Enum):
    PRICE = "price"
    FUNDAMENTAL = "fundamental"
    MACRO = "macro"
    NEWS = "news"
    INDEX_MEMBER = "index_member"
    CORPORATE_ACTION = "corporate_action"
    KNOWLEDGE = "knowledge"
    FEATURE = "feature"
    MODEL = "model"
    PREDICTION = "prediction"


REQUIRED_TIME_FIELDS = (
    "event_time", "published_time", "available_time",
    "collected_time", "revision_time",
)


@dataclass(frozen=True)
class TimeFields:
    event_time: Optional[date] = None
    published_time: Optional[date] = None
    available_time: Optional[date] = None
    collected_time: Optional[date] = None
    revision_time: Optional[date] = None
    source: str = "unknown"
    availability: Availability = Availability.KNOWN

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TimeFields":
        def parse(v):
            if v is None or v == "":
                return None
            if isinstance(v, datetime):
                return v.date()
            if isinstance(v, date):
                return v
            return date.fromisoformat(str(v)[:10])
        avail = parse(row.get("available_time"))
        return cls(
            event_time=parse(row.get("event_time")),
            published_time=parse(row.get("published_time")),
            available_time=avail,
            collected_time=parse(row.get("collected_time")),
            revision_time=parse(row.get("revision_time")),
            source=str(row.get("source", "unknown")),
            availability=(Availability.UNKNOWN if avail is None
                          else Availability.KNOWN),
        )


@dataclass(frozen=True)
class PredictionSpec:
    """每次预测的唯一身份与时点约束。"""
    prediction_year: int
    cutoff_date: date
    training_start_date: date
    knowledge_snapshot_id: str
    model_version: str
    feature_version: str
    strict_point_in_time: bool = True
    allow_future_data: bool = False
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["cutoff_date"] = self.cutoff_date.isoformat()
        d["training_start_date"] = self.training_start_date.isoformat()
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def for_year(cls, year: int, train_start_year: int,
                 snapshot_id: str, model_version: str,
                 feature_version: str) -> "PredictionSpec":
        return cls(
            prediction_year=year,
            cutoff_date=date(year - 1, 12, 31),
            training_start_date=date(train_start_year, 1, 1),
            knowledge_snapshot_id=snapshot_id,
            model_version=model_version,
            feature_version=feature_version,
        )


def parse_date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def require_known_availability(tf: TimeFields, spec: PredictionSpec) -> None:
    if spec.strict_point_in_time and tf.availability == Availability.UNKNOWN:
        raise ValueError(
            "unknown_availability: 数据缺少 available_time，严格模式下禁止使用"
        )
    if tf.available_time and tf.available_time > spec.cutoff_date and not spec.allow_future_data:
        raise ValueError(
            f"future_data_leak: available_time={tf.available_time} > cutoff={spec.cutoff_date}"
        )
