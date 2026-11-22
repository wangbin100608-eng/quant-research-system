"""SEC EDGAR 扩展适配器（v0.3.0 P0）：扩大公司覆盖 3 → 20+。

复用 sec_v2 的 filing 解析（available_time=真实 filed_date），仅扩展 CIK 列表。
"""
from __future__ import annotations

from .sec_v2 import SECAdapterV2

# 2000 年代主要美股上市公司 CIK（10 位补零）
CIKS_EXPANDED = [
    ("0000320193", "AAPL"), ("0000789019", "MSFT"), ("0001652044", "GOOGL"),
    ("0000019617", "JPM"),    ("0000315066", "C"),      ("0000729602", "WMT"),
    ("0000027419", "PG"),     ("0000066740", "KO"),     ("0000013404", "PFE"),
    ("0000051143", "IBM"),    ("0001045810", "NVDA"),   ("0000006281", "AMD"),
    ("0000791554", "INTC"),   ("0001018724", "AMZN"),   ("0001065280", "NFLX"),
    ("0001326801", "META"),   ("0000093410", "CVX"),    ("00001409", "XOM"),
    ("0000037996", "F"),      ("0000106634", "BA"),
]


class SECAdapterV3(SECAdapterV2):
    name = "sec_v3"

    def __init__(self, mode="auto", cfg=None, ciks=None, start=2000, end=2004):
        ciks = ciks or [(cik, sym, "US") for cik, sym in CIKS_EXPANDED]
        super().__init__(mode, cfg, ciks=ciks, start=start, end=end)
