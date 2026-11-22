"""A股扩展适配器（v0.3.0 P0）：扩大股票列表覆盖 + 个股历史日线 + 行业分类。

数据源（均为公开接口）：
  * 股票列表：push2delay.eastmoney.com clist（实测可用，1600+ 只）
  * 个股/指数日线：push2his.eastmoney.com kline（v0.2 已验证）
  * 行业分类：东方财富行业板块成分
所有行 source=real:*、synthetic=false；接口失败明确记缺口。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

KLINE = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
CLIST = "https://push2delay.eastmoney.com/api/qt/clist/get"


class EastmoneyExpandAdapter(RealAdapterBase):
    name = "eastmoney_expand"

    def __init__(self, mode="auto", cfg=None, start="20000101", end="20041231",
                 max_symbols=60, sample_indices=True):
        super().__init__(mode, cfg)
        self.start, self.end = start, end
        self.max_symbols = max_symbols
        self.sample_indices = sample_indices

    def fetch_stock_list(self) -> tuple[list[dict], list[dict]]:
        """股票基本信息：取沪市+深市主板列表，采样 max_symbols 只。"""
        rows, missing = [], []
        try:
            url = (f"{CLIST}?pn=1&pz=5000&po=1&np=1&fltt=2&invt=2&fid=f12"
                   f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&fields=f12,f14,f13")
            data = self.get(url, headers={"Referer": "https://quote.eastmoney.com/"}).json()
            diff = (data.get("data") or {}).get("diff") or []
            # 仅保留 2000 年代已上市的老代码：沪市 600/601，深市 000/001/002。
            # 排除 688(科创板2019+)、300(创业板2009+)、8xx(北交所) —— 它们在研究区间无数据。
            def is_old(code: str) -> bool:
                return code.startswith(("600", "601", "000", "001", "002"))
            old = [d for d in diff if is_old(str(d.get("f12", "")))]
            log.info("东财股票列表: total=%d old_era=%d", len(diff), len(old))
            # 均匀采样以控制抓取量
            step = max(1, len(old) // self.max_symbols)
            sampled = old[::step][:self.max_symbols]
            # 追加指数（研究区间确定有数据）
            for idx_code, idx_name, idx_secid in (("000001", "上证指数", "1.000001"),
                                                   ("399001", "深证成指", "0.399001")):
                sampled.insert(0, {"f12": idx_code, "f14": idx_name, "f13": 0,
                                   "_index": True, "_secid": idx_secid})
            for d in sampled:
                code = str(d.get("f12", ""))
                if not code:
                    continue
                market = "SH" if d.get("f13") == 1 else "SZ"
                is_index = d.get("_index", False)
                # 指数 secid 特例：上证 000001=1.000001，深成指 399001=0.399001
                secid = d.get("_secid") or (f"1.{code}" if market == "SH" else f"0.{code}")
                rows.append(self.real_row(
                    f"{code}.{market}", "CN_A", "info", "eastmoney",
                    date(2000, 1, 1), date(2000, 1, 1), date(2000, 1, 1),
                    {"code": code, "name": d.get("f14"), "exchange": market,
                     "security_type": "index" if is_index else "equity",
                     "secid": secid, "list_fixation": "index" if is_index else "sampled"},
                ))
            log.info("东财股票列表: total=%d sampled=%d", len(diff), len(sampled))
        except Exception as e:
            log.warning("东财股票列表失败: %s", e)
            missing.append({"domain": "info", "reason": str(e)})
        return rows, missing

    def _secid(self, row: dict) -> str | None:
        if row.get("secid"):
            return row["secid"]
        code = row.get("code")
        ex = row.get("exchange")
        if code and ex:
            return f"1.{code}" if ex == "SH" else f"0.{code}"
        return None

    def fetch_prices(self, symbols: list[dict]) -> tuple[list[dict], list[dict]]:
        rows, missing = [], []
        import json as _json
        for info in symbols:
            secid = self._secid(_json.loads(info["payload"]))
            if not secid:
                continue
            sym = info["symbol"]
            try:
                url = (f"{KLINE}?secid={secid}&fields1=f1,f2,f3"
                       f"&fields2=f51,f52,f53,f54,f55,f56,f57"
                       f"&klt=101&fqt=1&beg={self.start}&end={self.end}")
                data = self.get(url).json().get("data") or {}
                klines = data.get("klines") or []
                for line in klines:
                    p = line.split(",")
                    if len(p) < 6:
                        continue
                    d = date.fromisoformat(p[0])
                    rows.append(self.real_row(
                        sym, "CN_A", "price", "eastmoney", d, d, d,
                        {"close": float(p[2]), "volume": float(p[5] or 0),
                         "name": data.get("name")},
                    ))
                if not klines:
                    missing.append({"symbol": sym, "reason": "empty"})
            except Exception as e:
                missing.append({"symbol": sym, "reason": str(e)})
        log.info("东财个股日线: %d 只, %d 行", len(symbols), len(rows))
        return rows, missing

    def fetch(self) -> FetchResult:
        infos, missing = self.fetch_stock_list()
        prices, pmiss = self.fetch_prices(infos)
        rows = infos + prices
        missing += pmiss
        missing.append({"domain": "industry", "reason": "行业分类历史版本需专门接口，见 akshare 适配器"})
        return self.coverage(rows, extra={"symbols": len(infos), "sample": self.max_symbols},
                             missing=missing)
