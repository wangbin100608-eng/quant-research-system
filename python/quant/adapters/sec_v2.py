"""SEC EDGAR 真实适配器（v0.2.0）。

从 submissions recent + older 文件读取 2000-2004 的 10-K/10-Q，
available_time = 真实 filed_date（披露日），event_time = report_date。
这是财报“真实披露日期”的核心来源。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

SUB_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CIKS = [
    ("0000320193", "AAPL", "US"),
    ("0000789019", "MSFT", "US"),
    ("0001652044", "GOOGL", "US"),
]


class SECAdapterV2(RealAdapterBase):
    name = "sec_v2"

    def __init__(self, mode="auto", cfg=None, ciks=None, start=2000, end=2004):
        super().__init__(mode, cfg)
        self.ciks = ciks or CIKS
        self.start = start
        self.end = end

    def _collect(self, filings: dict, cik: str, symbol: str, market: str) -> list[dict]:
        rows = []
        forms = filings.get("form", [])
        fdates = filings.get("filingDate", [])
        rdates = filings.get("reportDate", [])
        accns = filings.get("accessionNumber", [])
        for form, fd, rd, acc in zip(forms, fdates, rdates, accns):
            if form not in ("10-K", "10-Q", "8-K"):
                continue
            if not fd or not (self.start <= int(fd[:4]) <= self.end):
                continue
            filed = date.fromisoformat(fd)
            try:
                report = date.fromisoformat(rd) if rd else filed
            except ValueError:
                report = filed
            rows.append(self.real_row(
                symbol, market, "fundamental" if form != "8-K" else "announcement",
                "sec_edgar", report, filed, filed,
                {"form": form, "accession": acc, "filed_date": fd,
                 "report_date": rd, "cik": cik,
                 "disclosure_lag_days": (filed - report).days},
            ))
        return rows

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for cik, symbol, market in self.ciks:
            try:
                hdr = {"User-Agent": "quant-research contact@example.com"}
                data = self.get(SUB_URL.format(cik=cik), headers=hdr).json()
                recent = data.get("filings", {}).get("recent", {})
                rows += self._collect(recent, cik, symbol, market)
                # older 文件（含 2000-2004）
                base = SUB_URL.format(cik=cik).rsplit("/", 1)[0]
                for finfo in data.get("filings", {}).get("files", []):
                    fname = finfo.get("name", "")
                    try:
                        older = self.get(f"{base}/{fname}", headers=hdr).json()
                        rows += self._collect(older, cik, symbol, market)
                    except Exception as e:
                        log.warning("SEC older %s 失败: %s", fname, e)
                log.info("SEC %s: 累计 %d 条披露", symbol, len(rows))
            except Exception as e:
                log.warning("SEC %s 失败: %s", symbol, e)
                missing.append({"cik": cik, "reason": str(e)})
        missing.append({"domain": "news", "reason": "SEC 提供 filing 而非新闻；新闻需专门数据源，记为缺口"})
        return self.coverage(rows, extra={"ciks": [c for c, _, _ in self.ciks]},
                             missing=missing)
