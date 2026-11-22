"""ALFRED 宏观历史版本适配器（v0.3.0 P0）。

通过 alfred.stlouisfed.org/graph/alfredgraph.csv?id=SERIES&vintage_date=YYYY-MM-DD
获取"当时可知"的历史版本。每个版本：
  event_time       = 观测期
  published_time   = vintage_date（真实版本日期，非估计）
  available_time   = vintage_date（严格模式以此为准）
  revision_time    = vintage_date
  payload.original_value = 该版本原始值
  payload.vintage_date   = 版本日期
  payload.time_basis     = "real_published"（严格区分估计）

严格区分：
  * real_published：来自 ALFRED vintage，真实发布日期
  * estimated：其他适配器（fred_v2）的估计滞后，标记且严格模式禁用
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

ALFRED = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
# 每季度/每月一个 vintage，覆盖 2000-2005（预测 2002-2004 需要此前各版本）
SERIES = ["CPIAUCSL", "GDP", "UNRATE", "DGS10", "FEDFUNDS", "PPIACO", "DEXCHUS"]


def _vintages(start: int, end: int) -> list[date]:
    out = []
    for y in range(start, end + 1):
        for m in (1, 4, 7, 10):  # 每季度一个快照
            out.append(date(y, m, 15))
    return out


class AlfredVintageAdapter(RealAdapterBase):
    name = "alfred_vintage"

    def __init__(self, mode="auto", cfg=None, series=None, start=2000, end=2004,
                 vintages=None):
        super().__init__(mode, cfg)
        self.series = series or SERIES
        self.start = start
        self.end = end
        self.vintages = vintages or _vintages(start - 1, end + 1)

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for sid in self.series:
            got = 0
            for vd in self.vintages:
                try:
                    url = (f"{ALFRED}?id={sid}&vintage_date={vd.isoformat()}"
                           f"&cosd={self.start}-01-01&coed={self.end}-12-31")
                    r = self.get(url)
                    reader = csv.reader(io.StringIO(r.text))
                    rows_iter = list(reader)
                    if len(rows_iter) < 2:
                        continue
                    header = rows_iter[0]
                    value_col = 1 if len(header) > 1 else None
                    if value_col is None:
                        continue
                    for line in rows_iter[1:]:
                        if len(line) <= value_col or line[value_col] in ("", ".", "NA"):
                            continue
                        try:
                            obs = date.fromisoformat(line[0])
                            val = float(line[value_col])
                        except (ValueError, IndexError):
                            continue
                        rows.append(self.real_row(
                            sid, "US", "macro_vintage", "alfred",
                            obs, vd, vd,
                            {"series": sid, "original_value": val,
                             "vintage_date": vd.isoformat(),
                             "time_basis": "real_published",
                             "revision_time": vd.isoformat(),
                             "header": header[value_col]},
                            revision=vd,
                        ))
                        got += 1
                except Exception as e:
                    log.warning("ALFRED %s@%s 失败: %s", sid, vd, e)
            if got == 0:
                missing.append({"series": sid, "reason": "no vintage data"})
            log.info("ALFRED %s: %d 版本化观测", sid, got)
        return self.coverage(
            rows,
            extra={"time_basis": "real_published",
                   "vintages": [v.isoformat() for v in self.vintages],
                   "note": "每个 vintage 为当时真实可知版本；available_time=vintage_date"},
            missing=missing)
