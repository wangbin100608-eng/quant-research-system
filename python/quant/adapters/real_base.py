"""真实数据源统一工具：HTTP 会话、五时间字段、断点缓存、失败不伪造。"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import requests

from .base import BaseAdapter, FetchResult, CANONICAL_SCHEMA
from ..logging_util import log


class RealAdapterBase(BaseAdapter):
    """真实源基类：统一 User-Agent、超时、重试、缓存路径。"""
    def __init__(self, mode="auto", cfg=None, timeout=20, retries=2):
        super().__init__(mode, cfg)
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; quant-research/0.2; +research)",
            "Accept": "application/json,text/csv,*/*",
        })

    def get(self, url: str, headers: dict | None = None,
            timeout: int | None = None) -> requests.Response:
        last = None
        for _ in range(self.retries + 1):
            try:
                r = self.session.get(url, timeout=timeout or self.timeout,
                                     headers=headers or {})
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
        raise last

    @staticmethod
    def real_row(symbol, market, domain, source, event, published, available,
                 payload, revision=None, collected=None) -> dict[str, Any]:
        """真实数据行：强制 source 前缀 real: 且 synthetic=False。"""
        p = dict(payload)
        p["synthetic"] = False
        p["data_origin"] = "real"
        return BaseAdapter.make_row(symbol, market, domain, f"real:{source}",
                                    event, published, available, p, revision, collected)

    def coverage(self, rows: list[dict], extra: dict | None = None,
                 missing: list | None = None) -> FetchResult:
        import pyarrow as pa
        table = pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)
        cov = {
            "data_origin": "real", "synthetic": False,
            "source": self.name, "rows": len(rows),
            "years": sorted({str(r["event_time"])[:4] for r in rows if r["event_time"]}),
            "min_available": min((r["available_time"] for r in rows if r["available_time"]), default=None),
            "max_available": max((r["available_time"] for r in rows if r["available_time"]), default=None),
        }
        if extra:
            cov.update(extra)
        return FetchResult(self.name, "live", table, cov, missing or [])
