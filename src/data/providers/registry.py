"""v0.7.0 P2：Provider 注册表。ACTIVE 适配器薄封装；BLOCKED 供应商如实标注。"""
from __future__ import annotations

from typing import Any

from .base import DataProvider


class AdapterProvider(DataProvider):
    """包装现有 v0.6 适配器，复用不重写。"""

    def __init__(self, name: str, adapter_cls, **kwargs):
        super().__init__(name)
        self.adapter_cls = adapter_cls
        self.kwargs = kwargs

    def get_data(self, request: dict[str, Any]) -> Any:
        return self.adapter_cls(**{**self.kwargs, **request}).fetch()


class BlockedProvider(DataProvider):
    def __init__(self, name: str, reason: str, url: str = ""):
        super().__init__(name, status="BLOCKED")
        self.reason = reason
        self.url = url

    def get_data(self, request: dict[str, Any]) -> Any:
        raise PermissionError(f"{self.name} BLOCKED: {self.reason}")

    def health_check(self) -> dict[str, Any]:
        return {"status": "BLOCKED", "provider": self.name,
                "reason": self.reason, "url": self.url}


def build_registry() -> dict[str, DataProvider]:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
    from quant.adapters import (YahooUSAdapter, SECAdapterV3, AlfredVintageAdapter,
                                EastmoneyCNAdapter, AkshareExtendedAdapter,
                                TencentIndexAdapter)
    reg: dict[str, DataProvider] = {
        "yahoo_us": AdapterProvider("yahoo_us", YahooUSAdapter),
        "sec_edgar": AdapterProvider("sec_edgar", SECAdapterV3),
        "alfred": AdapterProvider("alfred", AlfredVintageAdapter),
        "eastmoney_cn": AdapterProvider("eastmoney_cn", EastmoneyCNAdapter),
        "akshare_cn": AdapterProvider("akshare_cn", AkshareExtendedAdapter),
        "tencent_cn": AdapterProvider("tencent_cn", TencentIndexAdapter),
        "tushare_cn": BlockedProvider("tushare_cn", "需 token/积分权限，未核验"),
        "alpaca_us": BlockedProvider("alpaca_us", "需 API key/订阅；IEX vs SIP 未核验"),
        "sse_szse_csrc": BlockedProvider("sse_szse_csrc", "交易所官网适配器未实现"),
        "news_gdelt": BlockedProvider("news_gdelt", "历史覆盖缺口/限流"),
    }
    return reg
