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
    batch_index: int = -1          # -1=全量读取；>=0=流式批次号
    batch_size: int = 0
    cumulative_rows_in: int = 0    # 文件级累计（区别于单批）
    cumulative_rows_out: int = 0
    cumulative_dropped_future: int = 0
    cumulative_dropped_unknown: int = 0
    file_size_bytes: int = 0
    file_sha256: str = ""
    elapsed_seconds: float = 0.0
    interrupted: bool = False
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
        cutoff = self._spec_cutoff(spec)
        # v0.6 P0：无论用户指定哪些列，内部必须读取可用时间列；过滤后再投影回用户列
        schema_names = set(pq.ParquetFile(str(p)).schema_arrow.names)
        av_col = next((c for c in (FIELD_AVAILABLE, "available_time") if c in schema_names), None)
        if av_col is None and self.strict:
            raise TimeGuardReadError(f"no available time column in {p}")
        user_cols = (list(columns) if columns else
                     [c for c in schema_names if c != av_col])
        read_cols = list(dict.fromkeys(user_cols + ([av_col] if av_col else [])))
        table = pq.read_table(str(p), columns=read_cols)
        audit = ReadAudit(path=str(p), cutoff=iso_utc(cutoff),
                          columns=user_cols, rows_in=table.num_rows)
        if av_col is None:
            return table.select(user_cols), audit
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
        out = out.select(user_cols)  # 内部时间字段不强迫出现在结果中
        audit.rows_out = out.num_rows
        log.info("TimeGuardReader %s: %d -> %d (future=%d, unknown=%d)",
                 p.name, audit.rows_in, audit.rows_out,
                 audit.dropped_future, audit.dropped_unknown)
        return out, audit

    def stream_parquet(self, path: str | Path, spec: PredictionSpec | datetime,
                       columns: list[str] | None = None,
                       market_tz: str | None = None,
                       batch_size: int = 65536):
        """v0.5 P2：分批流式读取 + 分批时间过滤（低内存）。

        yield (RecordBatch, ReadAudit)；不将整列转 Python list，不构建大型索引列表。
        """
        p = Path(path)
        if not p.exists():
            raise TimeGuardReadError(f"file not found: {p}")
        cutoff = self._spec_cutoff(spec)
        pf = pq.ParquetFile(str(p))
        available_cols = set(pf.schema_arrow.names)
        av_col = next((c for c in (FIELD_AVAILABLE, "available_time") if c in available_cols), None)
        if av_col is None and self.strict:
            raise TimeGuardReadError(f"no available time column in {p}")
        user_cols = (list(columns) if columns
                     else [c for c in available_cols if c != av_col])
        read_cols = list(dict.fromkeys(user_cols + ([av_col] if av_col else [])))
        import hashlib, time as _time
        file_size = p.stat().st_size
        # 文件 SHA-256（流式，不整体读入内存）
        fh = hashlib.sha256()
        with open(p, "rb") as fp:
            for chunk in iter(lambda: fp.read(1 << 16), b""):
                fh.update(chunk)
        file_sha = fh.hexdigest()
        t_start = _time.perf_counter()
        cum_in = cum_out = cum_future = cum_unknown = 0
        batch_index = 0
        try:
            for batch in pf.iter_batches(batch_size=batch_size, columns=read_cols):
                b_in = batch.num_rows
                b_future = b_unknown = 0
                if av_col is None:
                    filtered = batch.select(user_cols)
                    b_out = b_in
                else:
                    av_values = batch.column(av_col).to_pylist()  # 仅当前批次
                    keep = []
                    for i, v in enumerate(av_values):
                        dt = to_utc(v, market_tz)
                        if dt is None:
                            b_unknown += 1
                            if self.strict:
                                raise TimeGuardReadError(
                                    f"unknown_availability batch={batch_index} row={i} in {p}")
                            continue
                        if dt <= cutoff:
                            keep.append(i)
                        else:
                            b_future += 1
                    filtered = (batch.take(pa.array(keep, type=pa.int64()))
                                if keep else batch.slice(0, 0)).select(user_cols)
                    b_out = filtered.num_rows
                cum_in += b_in; cum_out += b_out
                cum_future += b_future; cum_unknown += b_unknown
                yield filtered, ReadAudit(
                    path=str(p), cutoff=iso_utc(cutoff), columns=user_cols,
                    rows_in=b_in, rows_out=b_out,
                    dropped_future=b_future, dropped_unknown=b_unknown,  # 单批独立
                    batch_index=batch_index, batch_size=batch_size,
                    cumulative_rows_in=cum_in, cumulative_rows_out=cum_out,
                    cumulative_dropped_future=cum_future,
                    cumulative_dropped_unknown=cum_unknown,
                    file_size_bytes=file_size, file_sha256=file_sha,
                    elapsed_seconds=round(_time.perf_counter() - t_start, 4))
                batch_index += 1
        except Exception:
            # 中途失败：保存状态（通过异常携带审计上下文，不吞异常）
            raise TimeGuardReadError(
                f"stream interrupted at batch={batch_index}, "
                f"processed_in={cum_in}, processed_out={cum_out}")

    def filter_rows(self, rows: Iterable[dict[str, Any]],
                    spec: PredictionSpec | datetime,
                    market_tz: str | None = None) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            if self.validate_row(r, spec, market_tz):
                out.append(r)
        return out


    # ---------- v0.4.1 P1-6：SQL 时间门禁 ----------
    def query_sql(self, sql: str, spec_or_cutoff,
                  conn=None, params: list | None = None) -> list[dict[str, Any]]:
        """执行 SQL 并强制时间门禁。

        要求：SQL 结果必须包含 available_at（或 available_time）列；
        所有行 available_at <= cutoff（UTC）；否则严格模式拒绝。
        防止子查询绕过：无论内部 SQL 如何，外层统一按结果列过滤。
        """
        import duckdb
        cutoff = self._spec_cutoff(spec_or_cutoff)
        own = conn is None
        con = conn or duckdb.connect()
        try:
            rows = con.execute(sql, params or []).fetchall()
            cols = [d[0] for d in con.description] if con.description else []
        finally:
            if own:
                con.close()
        if not cols:
            if self.strict:
                raise TimeGuardReadError("query_sql: no result columns")
            return []
        av_idx = next((i for i, c in enumerate(cols)
                       if c in (FIELD_AVAILABLE, "available_time")), None)
        if av_idx is None and self.strict:
            raise TimeGuardReadError("query_sql: no available_at column (strict mode)")
        out = []
        for r in rows:
            if av_idx is not None:
                dt = to_utc(r[av_idx])
                if dt is None:
                    if self.strict:
                        raise TimeGuardReadError("query_sql: unknown_availability")
                    continue
                if dt > cutoff:
                    continue
            out.append(dict(zip(cols, r)))
        return out
