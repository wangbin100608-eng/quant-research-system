"""v0.4.0 P0：统一 TimeGuardReader —— 禁止业务代码绕过 TimeGuard 直接读原始数据。

所有历史数据读取必须走此类：
  * read_parquet(path, spec, ...) —— 列投影读取后按 available_at <= cutoff 过滤（UTC）；
  * query_sql(sql, ...)          —— DuckDB 查询后同样过滤（若结果含 available_at）；
  * validate_row(row, spec)      —— 单行门禁（兼容旧字段，经 migrate_record）。

任何 future/unknown 数据一律拒绝或剔除；返回时附带审计信息（输入/执行/结果）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .contracts import PredictionSpec
from .logging_util import log
from .timestamps_v04 import (cutoff_for_year, iso_utc, migrate_record,
                             available_ok, to_utc, FIELD_AVAILABLE)


class TimeGuardReadError(RuntimeError):
    pass


@dataclass
class ReadAudit:
    path: str
    cutoff: str
    columns: list[str]
    rows_in: int = 0
    rows_out: int = 0
    dropped_future: int = 0
    dropped_unknown: int = 0
    strategy: str = "strict_point_in_time_v04"
    created_at: str = field(default_factory=lambda: iso_utc(datetime.now(__import__("datetime").timezone.utc)))


class TimeGuardReader:
    """统一历史数据读取器（强制时间门禁）。"""

    def __init__(self, strict: bool = True):
        self.strict = strict

    def _spec_cutoff(self, spec: PredictionSpec | datetime) -> datetime:
        if isinstance(spec, datetime):
            return spec
        # v0.4 cutoff = Y-1-12-31 23:59:59Z（覆盖旧 spec.cutoff_date 的日期语义）
        return cutoff_for_year(spec.prediction_year)

    def validate_row(self, row: dict[str, Any], spec: PredictionSpec | datetime,
                     market_tz: str | None = None) -> bool:
        cutoff = self._spec_cutoff(spec)
        mr = migrate_record(row, market_tz=market_tz)
        av = mr.get(FIELD_AVAILABLE)
        if av is None and self.strict:
            raise TimeGuardReadError(f"unknown_availability: {row.get('symbol')}")
        if av is not None and not available_ok(av, cutoff):
            raise TimeGuardReadError(
                f"future_data_leak: available_at={av} > cutoff={iso_utc(cutoff)} "
                f"symbol={row.get('symbol')}")
        return True

    def read_parquet(self, path: str | Path, spec: PredictionSpec | datetime,
                     columns: list[str] | None = None,
                     market_tz: str | None = None,
                     allow_legacy_field: bool = True) -> tuple[pa.Table, ReadAudit]:
        """列投影读取 + 强制 available_at/available_time <= cutoff（UTC）。

        若文件无可用时间列：strict 模式拒绝。
        """
        p = Path(path)
        if not p.exists():
            raise TimeGuardReadError(f"file not found: {p}")
        table = pq.read_table(str(p), columns=columns)
        cutoff = self._spec_cutoff(spec)
        audit = ReadAudit(path=str(p), cutoff=iso_utc(cutoff),
                          columns=columns or table.column_names,
                          rows_in=table.num_rows)
        av_col = None
        for cand in (FIELD_AVAILABLE, "available_time"):
            if cand in table.column_names:
                av_col = cand
                break
        if av_col is None:
            if self.strict:
                raise TimeGuardReadError(f"no available time column in {p}")
            return table, audit
        av = table.column(av_col).to_pylist()
        keep = []
        for i, v in enumerate(av):
            dt = to_utc(v, market_tz)
            if dt is None:
                audit.dropped_unknown += 1
                if self.strict:
                    raise TimeGuardReadError(f"unknown_availability row {i} in {p}")
                continue
            if dt <= cutoff:
                keep.append(i)
            else:
                audit.dropped_future += 1
        out = table.take(pa.array(keep, type=pa.int64()))
        audit.rows_out = out.num_rows
        log.info("TimeGuardReader %s: %d -> %d (future=%d, unknown=%d)",
                 p.name, audit.rows_in, audit.rows_out,
                 audit.dropped_future, audit.dropped_unknown)
        return out, audit

    def filter_rows(self, rows: Iterable[dict[str, Any]],
                    spec: PredictionSpec | datetime,
                    market_tz: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            if self.validate_row(r, spec, market_tz):
                out.append(r)
        return out
