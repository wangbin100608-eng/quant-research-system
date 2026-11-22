"""Fixture 适配器：生成明确标注为合成的研究数据，保证端到端可运行可验收。

关键设计（用于验证时间隔离）：
  * 行情数据：available_time = 当日（收盘后可得），按 event_time=当日。
  * 财报：event_time=财报期末，published_time/available_time=真实披露日。
    - 特例：2001 年年度财报在 2002-03-15 披露 => 预测 2002 时不可见，
      可作为防泄漏测试的黄金样例。
  * 宏观：event_time=统计期，available_time=公布日（滞后 1~3 个月）。
  * 新闻：available_time = 发布时间。
  * 指数成分：按年度版本，available_time=调整生效公布日；保留退市股。
  * 公司行动：available_time=公告除权日。
所有数据在 payload 中标 "synthetic": true，报告中明确标注为 fixture，绝不冒充真实数据。
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Any

import pyarrow as pa

from .base import BaseAdapter, FetchResult, CANONICAL_SCHEMA

# 固定伪随机：可复现
_SEED = 20000101


def _mk(symbol, market, domain, source, event, published, available,
        payload, revision=None, collected=None):
    return BaseAdapter.make_row(symbol, market, domain, source, event,
                                published, available, payload, revision, collected)


def _trading_days(year: int) -> list[date]:
    d = date(year, 1, 1)
    end = date(year, 12, 31)
    days: list[date] = []
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class FixtureAdapter(BaseAdapter):
    """合成数据适配器（明确标注 synthetic）。"""
    name = "fixture"

    def __init__(self, mode="auto", cfg=None, start_year=2000, end_year=2004,
                 markets=None, symbols=None):
        super().__init__(mode, cfg)
        self.start = start_year
        self.end = end_year
        self.markets = markets or ["CN_A", "US"]
        self.symbols = symbols or {
            "CN_A": ["SH000001", "SZ399001", "600000.SH", "600519.SH", "000001.SZ"],
            "US": ["SPX", "IXIC", "AAPL", "MSFT", "GOOG"],
        }

    # ---------- 行情 ----------
    def _prices(self) -> list[dict[str, Any]]:
        rng = random.Random(_SEED)
        rows = []
        for mkt in self.markets:
            prices = {}
            for sym in self.symbols[mkt]:
                px = 100.0 + rng.random() * 50
                vol = 0.012 + rng.random() * 0.01
                drift = 0.0002 + rng.random() * 0.0004
                for y in range(self.start, self.end + 1):
                    for d in _trading_days(y):
                        px *= (1 + drift + rng.gauss(0, vol))
                        px = max(px, 1.0)
                        prices[(sym, d)] = px
                        rows.append(_mk(
                            sym, mkt, "price", "fixture", d, d, d,
                            {"close": round(px, 4),
                             "volume": int(1e6 * (0.5 + rng.random())),
                             "synthetic": True},
                        ))
        return rows

    # ---------- 财报（重点：披露时点滞后）----------
    def _fundamentals(self) -> list[dict[str, Any]]:
        rng = random.Random(_SEED + 1)
        rows = []
        # 披露滞后规则：年报次年 3~4 月；季报当季结束后 1 个月
        for mkt in self.markets:
            for sym in self.symbols[mkt]:
                for y in range(self.start, self.end + 1):
                    for q in (1, 2, 3, 4):
                        period_end = date(y, q * 3, 28)
                        if q == 4:
                            pub = date(y + 1, 3, 15)
                        else:
                            pub = date(y, q * 3 + 1, 15) if q < 4 else period_end
                            # q=1..3 -> pub 在 4/15,7/15,10/15
                        pub = min(pub, date(2099, 1, 1))
                        rows.append(_mk(
                            sym, mkt, "fundamental", "fixture", period_end, pub, pub,
                            {"fiscal_year": y, "quarter": q,
                             "revenue": round(1e8 * (1 + 0.05 * q + rng.random()), 2),
                             "net_income": round(1e7 * (0.8 + rng.random()), 2),
                             "eps": round(0.5 + rng.random(), 3),
                             "synthetic": True},
                        ))
        return rows

    # ---------- 宏观（公布滞后）----------
    def _macro(self) -> list[dict[str, Any]]:
        rng = random.Random(_SEED + 2)
        rows = []
        for y in range(self.start, self.end + 1):
            for m in range(1, 13):
                period = date(y, m, 1)
                pub = date(y, m, 12)   # 次月 12 日公布当月数据
                pub = pub + timedelta(days=31)
                # 简化：当月数据次月12日公布
                pub = date(y + (m // 12), (m % 12) + 1, 12)
                rows.append(_mk(
                    "MACRO", "GLOBAL", "macro", "fixture", period, pub, pub,
                    {"series": "CPI", "value": round(100 + y - 2000 + m * 0.1 + rng.random(), 3),
                     "synthetic": True},
                ))
                rows.append(_mk(
                    "MACRO", "US", "macro", "fixture", period, pub, pub,
                    {"series": "DGS10", "value": round(4.0 + rng.uniform(-1, 1), 3),
                     "synthetic": True},
                ))
        return rows

    # ---------- 新闻 ----------
    def _news(self) -> list[dict[str, Any]]:
        rng = random.Random(_SEED + 3)
        rows = []
        topical = ["盈利超预期", "监管政策变化", "新产品发布", "宏观数据公布", "行业景气回升"]
        for y in range(self.start, self.end + 1):
            for m in range(1, 13):
                for _ in range(3):
                    d = date(y, m, rng.randint(1, 28))
                    sym = rng.choice(self.symbols[rng.choice(self.markets)])
                    rows.append(_mk(
                        sym, "MIXED", "news", "fixture", d, d, d,
                        {"headline": f"{sym} {rng.choice(topical)}",
                         "sentiment": round(rng.uniform(-1, 1), 3),
                         "synthetic": True},
                    ))
        return rows

    # ---------- 指数成分（历史版本 + 退市股）----------
    def _index_members(self) -> list[dict[str, Any]]:
        rows = []
        membership = {
            "CN_A": {"SH000001": ["600000.SH", "600519.SH", "000001.SZ", "600001.SH"]},
            "US": {"SPX": ["AAPL", "MSFT", "GOOG", "ENRON"]},  # ENRON 为虚构退市股占位
        }
        for y in range(self.start, self.end + 1):
            pub = date(y, 1, 5)  # 年初调整公布
            for mkt, idxmap in membership.items():
                for idx, members in idxmap.items():
                    for sym in members:
                        rows.append(_mk(
                            idx, mkt, "index_member", "fixture",
                            date(y, 1, 1), pub, pub,
                            {"member": sym, "effective_year": y,
                             "delisted": sym in ("600001.SH", "ENRON"),
                             "synthetic": True},
                        ))
        return rows

    # ---------- 公司行动 ----------
    def _corporate_actions(self) -> list[dict[str, Any]]:
        rows = []
        for mkt in self.markets:
            for sym in self.symbols[mkt]:
                for y in range(self.start, self.end + 1):
                    d = date(y, 6, 15)
                    rows.append(_mk(
                        sym, mkt, "corporate_action", "fixture", d, d, d,
                        {"action": "dividend", "amount": 0.1, "synthetic": True},
                    ))
        return rows

    def fetch(self) -> FetchResult:
        rows = []
        rows += self._prices()
        rows += self._fundamentals()
        rows += self._macro()
        rows += self._news()
        rows += self._index_members()
        rows += self._corporate_actions()
        table = pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)
        coverage = {
            "source": "fixture", "synthetic": True,
            "years": [self.start, self.end],
            "rows": len(rows),
            "markets": self.markets,
            "domains": sorted({r["domain"] for r in rows}),
            "fingerprint": self.fingerprint(table),
        }
        return FetchResult("fixture", "fixture", table, coverage)
