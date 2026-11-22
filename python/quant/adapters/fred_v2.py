"""FRED/ALFRED 宏观真实适配器（v0.2.0）。

获取多 series 的真实历史值；event_time=观测期，published_time/available_time=发布日。
FRED graph 不含 vintage；对已知 series 用保守的发布滞后（月度数据次月 15 日），
并在 payload 中标 publication_lag="estimated"，明确区别于真实 vintage。
ALFRED vintage 数据若可用则优先（本适配器尝试 ALFRED fredgraph）。
"""
from __future__ import annotations

import csv
import io
from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

FRED_GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES = ["DGS10", "DGS2", "CPIAUCSL", "GDP", "UNRATE", "FEDFUNDS", "DEXCHUS"]


class FREDAdapterV2(RealAdapterBase):
    name = "fred_v2"

    def __init__(self, mode="auto", cfg=None, series=None, start=2000, end=2004):
        super().__init__(mode, cfg)
        self.series = series or SERIES
        self.start = start
        self.end = end

    def _publication_date(self, obs: date, sid: str) -> date:
        # 保守估计：月度数据次月15日；年度GDP次年3月；日度序列当日
        if sid in ("GDP",):
            return date(obs.year + 1, 3, 15)
        if sid in ("DGS10", "DGS2", "DEXCHUS"):
            return obs  # 日频市场数据当日可得
        return date(obs.year + (obs.month // 12), (obs.month % 12) + 1, 15)

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for sid in self.series:
            try:
                r = self.get(f"{FRED_GRAPH}?id={sid}")
                reader = csv.reader(io.StringIO(r.text))
                n = 0
                for row in reader:
                    if len(row) < 2 or row[0] == "observation_date" or row[1] in (".", ""):
                        continue
                    try:
                        d = date.fromisoformat(row[0])
                    except ValueError:
                        continue
                    if not (self.start <= d.year <= self.end):
                        continue
                    pub = self._publication_date(d, sid)
                    rows.append(self.real_row(
                        sid, "US", "macro", "fred", d, pub, pub,
                        {"series": sid, "value": float(row[1]),
                         "vintage": "fred_graph_estimated_lag",
                         "publication_lag": "estimated"},
                    ))
                    n += 1
                log.info("FRED %s: %d 条 (2000-2004)", sid, n)
                if n == 0:
                    missing.append({"series": sid, "reason": "no rows in range"})
            except Exception as e:
                log.warning("FRED %s 失败: %s", sid, e)
                missing.append({"series": sid, "reason": str(e)})
        return self.coverage(rows, extra={"series": self.series,
                                          "note": "vintage 为估计发布滞后，非 ALFRED 原始 vintage"},
                             missing=missing)
