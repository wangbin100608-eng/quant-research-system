"""v0.4.0 P0：统一时间内核（UTC）。

规则（强制）：
  * 所有时间以 UTC 存储，datetime 一律 timezone-aware（tzinfo=timezone.utc）。
  * 统一字段：event_time / published_at / available_at / ingested_at / revision_time。
  * 兼容旧字段：published_time -> published_at、available_time -> available_at、
    collected_time -> ingested_at（仅迁移层使用，业务代码必须用新字段）。
  * cutoff(Y) = Y-1-12-31T23:59:59Z。
  * available_at <= cutoff（UTC 精确比较，不允许仅按日期截断）。
  * 市场时区：各市场发布时按当地 timezone 转 UTC（IANA zoneinfo）。
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc

# 统一字段（v0.4）
FIELD_EVENT = "event_time"
FIELD_PUBLISHED = "published_at"
FIELD_AVAILABLE = "available_at"
FIELD_INGESTED = "ingested_at"
FIELD_REVISION = "revision_time"

# 旧字段 -> 新字段（迁移映射）
LEGACY_FIELD_MAP = {
    "published_time": FIELD_PUBLISHED,
    "available_time": FIELD_AVAILABLE,
    "collected_time": FIELD_INGESTED,
    "event_time": FIELD_EVENT,          # 同名保留
    "revision_time": FIELD_REVISION,    # 同名保留
}

MARKET_TZ = {
    "CN_A": "Asia/Shanghai",
    "US": "America/New_York",
    "GLOBAL": "UTC",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_utc(value: Any, market_tz: str | None = None) -> datetime | None:
    """把 date/datetime/ISO 字符串转 UTC aware datetime。

    * date -> 当日 00:00:00 UTC（无时刻信息时保守取最早，调用方应提供完整时刻）
    * naive datetime + market_tz -> 按市场时区解释后转 UTC
    * aware datetime -> 直接转 UTC
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    elif isinstance(value, str):
        s = value.strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            try:
                return datetime.combine(date.fromisoformat(value[:10]), time.min, tzinfo=UTC)
            except ValueError:
                return None
    else:
        return None
    if dt.tzinfo is None:
        # 接受 IANA 时区名或市场代码（CN_A / US / GLOBAL）
        zone_name = MARKET_TZ.get(market_tz, market_tz) if market_tz else "UTC"
        tz = ZoneInfo(zone_name)
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def iso_utc(value: datetime | None) -> str | None:
    """输出 ISO-8601 UTC（以 +00:00 结尾，统一格式）。"""
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def cutoff_for_year(year: int) -> datetime:
    """预测年份 Y 的严格截止：Y-1-12-31T23:59:59Z。"""
    return datetime(year - 1, 12, 31, 23, 59, 59, tzinfo=UTC)


def cutoff_iso(year: int) -> str:
    return iso_utc(cutoff_for_year(year))


def migrate_record(record: dict[str, Any], market_tz: str | None = None) -> dict[str, Any]:
    """旧字段 -> v0.4 统一字段（不删除旧字段，便于回滚/对照）。"""
    out = dict(record)
    for old, new in LEGACY_FIELD_MAP.items():
        if old in out and new not in out:
            out[new] = iso_utc(to_utc(out[old], market_tz))
    out["schema_v04"] = True
    out["ingested_at"] = out.get(FIELD_INGESTED) or iso_utc(utcnow())
    return out


def available_ok(available_at: Any, cutoff: datetime,
                 market_tz: str | None = None) -> bool:
    """统一准入判断：available_at <= cutoff（UTC）。None 一律 False。"""
    av = to_utc(available_at, market_tz)
    return av is not None and av <= cutoff
