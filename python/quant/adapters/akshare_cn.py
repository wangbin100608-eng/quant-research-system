"""AKShare A股适配器骨架：仅在依赖可用且网络可达时抓取，否则明确报告缺口。"""
from __future__ import annotations

from datetime import date
from typing import Any

from .base import BaseAdapter, FetchResult
from ..logging_util import log


class AkshareCNAdapter(BaseAdapter):
    name = "akshare_cn"

    def __init__(self, mode="auto", cfg=None, symbols=None):
        super().__init__(mode, cfg)
        self.symbols = symbols or ["600000", "000001"]

    def fetch(self) -> FetchResult:
        import pyarrow as pa
        from .base import CANONICAL_SCHEMA
        rows: list[dict[str, Any]] = []
        errors = []
        try:
            import akshare as ak  # noqa
        except Exception as e:
            errors.append(f"akshare not installed: {e}")
            log.warning("AKShare 不可用，A股实时源跳过（不伪造数据）: %s", e)
        else:
            for sym in self.symbols:
                try:
                    df = ak.stock_zh_a_hist(symbol=sym, period="daily",
                                            start_date="20000101", end_date="20041231",
                                            adjust="")
                    for _, r in df.iterrows():
                        d = date.fromisoformat(str(r["日期"]))
                        rows.append(self.make_row(
                            sym, "CN_A", "price", "akshare_cn", d, d, d,
                            {"close": float(r["收盘"]), "volume": float(r["成交量"]),
                             "synthetic": False},
                        ))
                except Exception as e:
                    errors.append(f"{sym}: {e}")
        table = pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)
        return FetchResult("akshare_cn", "live" if rows else "unavailable", table,
                           {"source": "akshare_cn", "rows": len(rows),
                            "errors": errors, "synthetic": False},
                           [{"reason": e} for e in errors])
