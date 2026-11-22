"""v0.4 完善：腾讯财经指数日线适配器（真实数据）。

东财 kline 限流 BLOCKED 后找到的稳定替代源：
  https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=CODE,day,START,END,N,fq
实测：sh000001/sz399001 有 2000-2004 各 1200 根真实日线；
sh000300/sh000905/sh000852 在该区间无数据（真实历史：沪深300 2005 发布、中证500 2007 发布），明确记缺口。
所有行 source=real:tencent、synthetic=false；保存原始响应 SHA-256。
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TARGETS = [
    ("sh000001", "SH000001", "上证指数"),
    ("sz399001", "SZ399001", "深证成指"),
    ("sh000300", "SH000300", "沪深300"),
    ("sh000905", "SH000905", "中证500"),
    ("sh000852", "SH000852", "中证1000"),
]


class TencentIndexAdapter(RealAdapterBase):
    name = "tencent_index"

    def __init__(self, mode="auto", cfg=None, start="2000-01-01", end="2004-12-31"):
        super().__init__(mode, cfg)
        self.start, self.end = start, end

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []
        for code, sym, name in TARGETS:
            try:
                url = f"{KLINE}?param={code},day,{self.start},{self.end},1500,qfq"
                resp = self.get(url)
                raw_hash = hashlib.sha256(resp.content).hexdigest()
                data = resp.json().get("data", {}).get(code, {})
                arr = data.get("day") or data.get("qfqday") or []
                n = 0
                for item in arr:
                    if len(item) < 6:
                        continue
                    try:
                        d = date.fromisoformat(item[0])
                        close = float(item[2]); vol = float(item[5])
                    except (ValueError, IndexError):
                        continue
                    if close <= 0 or vol < 0:
                        continue
                    # 日线收盘后可得；UTC 时刻按本地 15:00 CST -> 07:00Z 保守近似
                    rows.append(self.real_row(
                        sym, "CN_A", "price", "tencent", d, d, d,
                        {"close": close, "open": float(item[1]),
                         "high": float(item[3]), "low": float(item[4]),
                         "volume": vol, "name": name,
                         "raw_sha256": raw_hash,
                         "adjustment": "qfq_from_tencent"},
                    ))
                    n += 1
                log.info("Tencent %s: %d 根真实日线", sym, n)
                if n == 0:
                    missing.append({"symbol": sym,
                                    "reason": "no data in 2000-2004 (index published later or interface gap)"})
            except Exception as e:
                log.warning("Tencent %s 失败: %s", sym, e)
                missing.append({"symbol": sym, "reason": str(e)})
        return self.coverage(rows, extra={"source_detail": "tencent_fqkline",
                                          "adjustment": "qfq"},
                             missing=missing)
