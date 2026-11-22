"""SEC EDGAR submissions 适配器骨架（真实披露时间 filed date 作为 available_time）。"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .base import BaseAdapter, FetchResult
from ..logging_util import log


class SecEdgarAdapter(BaseAdapter):
    name = "sec_edgar"

    def __init__(self, mode="auto", cfg=None, ciks=None,
                 base_url="https://data.sec.gov/submissions",
                 user_agent="research quant-research contact@example.com"):
        super().__init__(mode, cfg)
        self.ciks = ciks or ["0000320193"]
        self.base_url = base_url
        self.user_agent = user_agent

    def fetch(self) -> FetchResult:
        import pyarrow as pa
        from .base import CANONICAL_SCHEMA
        rows: list[dict[str, Any]] = []
        missing = []
        errors = []
        s = requests.Session()
        s.headers.update({"User-Agent": self.user_agent,
                          "Accept-Encoding": "gzip, deflate"})
        for cik in self.ciks:
            try:
                r = s.get(f"{self.base_url}/CIK{cik}.json", timeout=20)
                r.raise_for_status()
                data = r.json()
                recent = data.get("filings", {}).get("recent", {})
                forms = recent.get("form", [])
                fdates = recent.get("filingDate", [])
                rdates = recent.get("reportDate", [])
                accns = recent.get("accessionNumber", [])
                count = 0
                for form, fd, rd, acc in zip(forms, fdates, rdates, accns):
                    if form not in ("10-K", "10-Q"):
                        continue
                    if fd > "2004-12-31":  # 研究区间过滤，避免未来数据进入原始层
                        continue
                    filed = date.fromisoformat(fd)
                    report = date.fromisoformat(rd) if rd else None
                    rows.append(self.make_row(
                        cik, "US", "fundamental", "sec_edgar",
                        report, filed, filed,
                        {"form": form, "accession": acc,
                         "filed_date": fd, "report_date": rd,
                         "synthetic": False},
                    ))
                    count += 1
                log.info("SEC %s: %d filings", cik, count)
            except Exception as e:
                errors.append(f"{cik}: {e}")
                missing.append({"cik": cik, "reason": str(e)})
                log.warning("SEC %s 抓取失败: %s", cik, e)
        table = pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)
        return FetchResult("sec_edgar", "live" if rows else "unavailable", table,
                           {"source": "sec_edgar", "rows": len(rows),
                            "errors": errors, "synthetic": False}, missing)
