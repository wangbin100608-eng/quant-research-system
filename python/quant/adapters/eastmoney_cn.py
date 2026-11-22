"""A股真实数据适配器（东方财富公开接口）。

获取：上证指数/深证成指的 2000-2004 真实日线；个股基本信息（名称）。
指数成分股/退市股/财报披露日若公开接口不可稳定获取，明确记录缺口，不伪造。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pyarrow as pa

from .real_base import RealAdapterBase
from ..logging_util import log

EASTMONEY_KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

# secid -> (symbol, market, 名称)
TARGETS = [
    ("1.000001", "SH000001", "CN_A", "上证指数"),
    ("0.399001", "SZ399001", "CN_A", "深证成指"),
    ("1.000300", "SH000300", "CN_A", "沪深300"),
]


class EastmoneyCNAdapter(RealAdapterBase):
    name = "eastmoney_cn"

    def __init__(self, mode="auto", cfg=None, start="20000101", end="20041231"):
        super().__init__(mode, cfg)
        self.start = start
        self.end = end

    def fetch_prices(self) -> tuple[list[dict], list[dict]]:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for secid, symbol, market, name in TARGETS:
            try:
                params = {
                    "secid": secid, "fields1": "f1,f2,f3",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "klt": "101", "fqt": "1", "beg": self.start, "end": self.end,
                }
                url = EASTMONEY_KLINE + "?" + "&".join(f"{k}={v}" for k, v in params.items())
                data = self.get(url).json().get("data") or {}
                klines = data.get("klines") or []
                for line in klines:
                    parts = line.split(",")
                    if len(parts) < 6:
                        continue
                    d = date.fromisoformat(parts[0])
                    close = float(parts[2])
                    vol = float(parts[5]) if parts[5] not in ("", "-") else 0.0
                    rows.append(self.real_row(
                        symbol, market, "price", "eastmoney", d, d, d,
                        {"close": close, "open": float(parts[1]),
                         "high": float(parts[3]), "low": float(parts[4]),
                         "volume": vol, "name": name},
                    ))
                log.info("Eastmoney %s: %d 根日线", symbol, len(klines))
                if not klines:
                    missing.append({"symbol": symbol, "reason": "empty_response"})
            except Exception as e:
                log.warning("Eastmoney %s 失败: %s", symbol, e)
                missing.append({"symbol": symbol, "reason": str(e)})
        return rows, missing

    def fetch_info(self) -> list[dict]:
        """基本信息：从行情结果附带 name；独立记录为 info 域。"""
        rows = []
        for secid, symbol, market, name in TARGETS:
            rows.append(self.real_row(
                symbol, market, "info", "eastmoney",
                date(2000, 1, 1), date(2000, 1, 1), date(2000, 1, 1),
                {"name": name, "symbol": symbol, "exchange": "SH" if symbol.startswith("SH") else "SZ",
                 "security_type": "index"},
            ))
        return rows

    def fetch(self) -> FetchResult:
        rows, missing = self.fetch_prices()
        rows += self.fetch_info()
        missing += [
            {"domain": "index_member", "reason": "东方财富历史指数成分股接口不稳定；本适配器未获取，记为缺口"},
            {"domain": "delisted", "reason": "退市股名单需专门接口；本适配器未获取，记为缺口"},
            {"domain": "fundamental", "reason": "A股财报真实披露日需专门接口；本适配器未获取，记为缺口"},
        ]
        return self.coverage(rows, extra={"market": "CN_A", "index_count": len(TARGETS)},
                             missing=missing)
