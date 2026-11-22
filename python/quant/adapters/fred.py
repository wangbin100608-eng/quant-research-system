"""FRED / ALFRED 宏观数据适配器。

关键：使用 FRED graph CSV（无需 key）获取观测值；同时记录 publication 口径。
真实 vintage（当时公布版本）优先使用 ALFRED；本适配器在 mode=live 时尝试抓取，
失败则不伪造，明确记录 missing_ranges 与覆盖率。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .base import BaseAdapter, FetchResult
from ..logging_util import log

FRED_META = {
    "DGS10": ("US", {"series": "DGS10", "unit": "percent"}),
    "CPIAUCSL": ("US", {"series": "CPIAUCSL", "unit": "index"}),
    "GDP": ("US", {"series": "GDP", "unit": "billions"}),
    "UNRATE": ("US", {"series": "UNRATE", "unit": "percent"}),
}


class FredAdapter(BaseAdapter):
    name = "fred"

    def __init__(self, mode="auto", cfg=None, series=None,
                 base_url="https://fred.stlouisfed.org/graph/fredgraph.csv"):
        super().__init__(mode, cfg)
        self.base_url = base_url
        self.series = series or list(FRED_META)

    def fetch(self) -> FetchResult:
        import csv
        import io
        import pyarrow as pa
        from .base import CANONICAL_SCHEMA
        rows: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        errors = []
        s = requests.Session()
        s.headers.update({"User-Agent": "quant-research/0.1"})
        for sid in self.series:
            try:
                url = f"{self.base_url}?id={sid}"
                r = s.get(url, timeout=20)
                r.raise_for_status()
                reader = csv.DictReader(io.StringIO(r.text))
                count = 0
                for row in reader:
                    k = list(row.keys())
                    if len(k) < 2:
                        continue
                    dval = row[k[0]].strip()
                    v = row[k[1]].strip()
                    if not dval or v in ("", ".", "NA"):
                        continue
                    d = date.fromisoformat(dval)
                    # FRED graph 不含 publication lag，保守以 observation 月末次月12日
                    pub = date(d.year + (d.month // 12), (d.month % 12) + 1, 12)
                    market, payload = FRED_META.get(sid, ("US", {"series": sid}))
                    payload = dict(payload)
                    payload.update({"value": float(v), "vintage": "fred_graph",
                                    "synthetic": False})
                    rows.append(self.make_row(sid, market, "macro", "fred",
                                              d, pub, pub, payload))
                    count += 1
                log.info("FRED %s: %d rows", sid, count)
                if count == 0:
                    missing.append({"series": sid, "reason": "empty_response"})
            except Exception as e:  # 不伪造
                errors.append(f"{sid}: {e}")
                missing.append({"series": sid, "reason": str(e)})
                log.warning("FRED %s 抓取失败: %s", sid, e)
        table = pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)
        mode_used = "live" if rows else "unavailable"
        cov = {"source": "fred", "mode": mode_used, "rows": len(rows),
               "series": self.series, "errors": errors,
               "synthetic": False}
        return FetchResult("fred", mode_used, table, cov, missing)
