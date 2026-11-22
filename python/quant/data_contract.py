"""Compatibility re-export: 数据契约集中定义在 quant.contracts。"""
from .contracts import (  # noqa: F401
    REQUIRED_TIME_FIELDS, Availability, DataDomain,
    TimeFields, PredictionSpec, parse_date, require_known_availability,
)
