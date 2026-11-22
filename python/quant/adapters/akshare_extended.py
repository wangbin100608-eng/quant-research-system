"""AKShare 扩展适配器（v0.3.0 P0）：成分股/退市股/行业分类/A股财报披露。

AKShare 接口可能被上游限流；本适配器逐个探测，失败明确记缺口，绝不用合成数据补齐。
成功抓取的行均标 source=real:akshare、synthetic=false。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log


class AkshareExtendedAdapter(RealAdapterBase):
    name = "akshare_extended"

    def __init__(self, mode="auto", cfg=None, index_symbols=None, max_rows=500):
        super().__init__(mode, cfg)
        self.index_symbols = index_symbols or ["000300", "000016", "000905"]
        self.max_rows = max_rows

    def _emit(self, rows, fn, domain, symbol_mapper, missing, label):
        try:
            df = fn()
            n = 0
            for _, r in df.iterrows():
                if n >= self.max_rows:
                    break
                d = symbol_mapper(r)
                if not d:
                    continue
                rows.append(self.real_row(
                    d["symbol"], d.get("market", "CN_A"), domain, "akshare",
                    d.get("event"), d.get("published"), d.get("available"),
                    d.get("payload", {}),
                ))
                n += 1
            log.info("AKShare %s: %d 行", label, n)
            if n == 0:
                missing.append({"domain": domain, "reason": f"{label} empty"})
        except Exception as e:
            log.warning("AKShare %s 失败: %s", label, e)
            missing.append({"domain": domain, "reason": f"{label}: {type(e).__name__}: {str(e)[:80]}"})

    def fetch(self) -> FetchResult:
        import akshare as ak
        rows: list[dict[str, Any]] = []
        missing: list[dict] = []

        # 1) 指数成分股（当前名单，含纳入日期；标注为 current members）
        for idx in self.index_symbols:
            self._emit(
                rows,
                lambda s=idx: ak.index_stock_cons(symbol=s),
                "index_member",
                lambda r, s=idx: {
                    "symbol": f"{str(r.get('品种代码','')).zfill(6)}.CN",
                    "market": "CN_A",
                    "event": date.fromisoformat(str(r.get("纳入日期", "2000-01-01"))[:10])
                             if r.get("纳入日期") else date(2000, 1, 1),
                    "published": date(2000, 1, 1),
                    "available": date(2000, 1, 1),
                    "payload": {"index": s, "member": str(r.get("品种代码")),
                                "name": r.get("品种名称"), "basis": "current_members"},
                },
                missing, f"index_cons_{idx}")

        # 2) 沪市退市（真实退市日期作为 available_time）
        def _pdate(v, default="2000-01-01"):
            try:
                return date.fromisoformat(str(v)[:10])
            except Exception:
                return date.fromisoformat(default)
        self._emit(rows, ak.stock_info_sh_delist, "delisted",
                   lambda r: {
                       "symbol": f"{str(r.get('公司代码','')).zfill(6)}.SH",
                       "market": "CN_A",
                       "event": _pdate(r.get("上市日期")),
                       "published": _pdate(r.get("暂停上市日期")),
                       "available": _pdate(r.get("暂停上市日期")),
                       "payload": {"code": r.get("公司代码"), "name": r.get("公司简称"),
                                   "list_date": str(r.get("上市日期")),
                                   "delist_date": str(r.get("暂停上市日期")),
                                   "basis": "real_delisting_record"},
                   }, missing, "sh_delist")

        # 3) 深市退市（真实终止上市日期）
        self._emit(rows, lambda: ak.stock_info_sz_delist(symbol="终止上市公司"),
                   "delisted",
                   lambda r: {
                       "symbol": f"{str(r.get('证券代码','')).zfill(6)}.SZ",
                       "market": "CN_A",
                       "event": _pdate(r.get("上市日期")),
                       "published": _pdate(r.get("终止上市日期")),
                       "available": _pdate(r.get("终止上市日期")),
                       "payload": {"code": r.get("证券代码"), "name": r.get("证券简称"),
                                   "list_date": str(r.get("上市日期")),
                                   "delist_date": str(r.get("终止上市日期")),
                                   "basis": "real_delisting_record"},
                   }, missing, "sz_delist")

        # 4) 申万行业分类（当前版本；历史版本接口不可用则记缺口）
        self._emit(rows, ak.index_first_info_sw, "industry",
                   lambda r: {
                       "symbol": str(r.get("行业代码", "SW")),
                       "market": "CN_A", "event": date(2000, 1, 1),
                       "published": date(2000, 1, 1), "available": date(2000, 1, 1),
                       "payload": {"industry_code": r.get("行业代码"),
                                   "industry_name": r.get("行业名称"),
                                   "basis": "current_classification"},
                   }, missing, "sw_industry")
        missing.append({"domain": "industry_history",
                        "reason": "申万历史行业分类版本需专门接口；当前仅得当前版本"})

        # 5) A股财报披露时间（AKShare stock_report_disclosure 按 market/period，
        #    历史 2000-2004 年报披露日覆盖有限，明确标注所获年份）
        self._emit(rows, lambda: ak.stock_report_disclosure(market="沪市", period="2001年报"),
                   "fundamental_disclosure",
                   lambda r: {
                       "symbol": f"{str(r.get('股票代码','')).zfill(6)}.SH",
                       "market": "CN_A",
                       "event": _pdate("2001-12-31"),
                       "published": _pdate(r.get("披露日期")),
                       "available": _pdate(r.get("披露日期")),
                       "payload": {"report_period": "2001年报",
                                   "disclosure_date": str(r.get("披露日期")),
                                   "code": str(r.get("股票代码"))},
                   }, missing, "report_disclosure_2001")

        return self.coverage(rows, extra={"index_symbols": self.index_symbols},
                             missing=missing)
