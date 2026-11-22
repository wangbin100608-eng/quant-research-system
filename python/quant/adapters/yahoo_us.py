"""美股真实数据适配器（Yahoo Finance chart API）。

获取：标普500/纳斯达克/道指 2000-2004 真实日线；基本信息。
个股（AAPL/MSFT）额外获取。SEC 财报披露日由 SEC 适配器负责。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}"

TARGETS = [
    ("^GSPC", "SPX", "US", "S&P 500"),
    ("^IXIC", "IXIC", "US", "NASDAQ Composite"),
    ("^DJI", "DJI", "US", "Dow Jones"),
    ("AAPL", "AAPL", "US", "Apple Inc."),
    ("MSFT", "MSFT", "US", "Microsoft Corp."),
]
P1 = 946684800   # 2000-01-01
P2 = 1104537600  # 2005-01-01


class YahooUSAdapter(RealAdapterBase):
    name = "yahoo_us"

    def fetch_prices(self) -> tuple[list[dict], list[dict]]:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for ysym, symbol, market, name in TARGETS:
            try:
                url = YAHOO_CHART.format(ysym) + f"?period1={P1}&period2={P2}&interval=1d"
                r = self.get(url, headers={"User-Agent": "Mozilla/5.0"}).json()
                res = (r.get("chart", {}).get("result") or [None])[0]
                if not res:
                    missing.append({"symbol": symbol, "reason": str(r.get("chart", {}).get("error"))})
                    continue
                ts = res.get("timestamp", [])
                quotes = res["indicators"]["quote"][0]
                n = 0
                for i, t in enumerate(ts):
                    close = quotes["close"][i]
                    if close is None:
                        continue
                    d = datetime.fromtimestamp(t, tz=timezone.utc).date()
                    vol = quotes.get("volume", [0] * len(ts))[i] or 0
                    rows.append(self.real_row(
                        symbol, market, "price", "yahoo", d, d, d,
                        {"close": float(close), "volume": float(vol), "name": name},
                    ))
                    n += 1
                log.info("Yahoo %s: %d 根日线", symbol, n)
                if n == 0:
                    missing.append({"symbol": symbol, "reason": "empty"})
            except Exception as e:
                log.warning("Yahoo %s 失败: %s", symbol, e)
                missing.append({"symbol": symbol, "reason": str(e)})
        return rows, missing

    def fetch_info(self) -> list[dict]:
        rows = []
        for ysym, symbol, market, name in TARGETS:
            rows.append(self.real_row(
                symbol, market, "info", "yahoo",
                date(2000, 1, 1), date(2000, 1, 1), date(2000, 1, 1),
                {"name": name, "symbol": symbol, "yahoo_ticker": ysym,
                 "security_type": "index" if ysym.startswith("^") else "equity"},
            ))
        return rows

    def fetch(self) -> FetchResult:
        rows, missing = self.fetch_prices()
        rows += self.fetch_info()
        missing += [
            {"domain": "index_member", "reason": "Yahoo 不提供历史指数成分股；记为缺口"},
            {"domain": "delisted", "reason": "退市股名单需专门数据源；记为缺口"},
        ]
        return self.coverage(rows, extra={"market": "US", "symbol_count": len(TARGETS)},
                             missing=missing)
