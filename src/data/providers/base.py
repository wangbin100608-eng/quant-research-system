"""v0.7.0 P2：统一 DataProvider 接口。所有数据源适配器必须实现此契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class DataProvider(ABC):
    """可替换数据源基类。业务代码不得写死供应商 API。"""

    def __init__(self, name: str, status: str = "ACTIVE"):
        self.name = name
        self.status = status

    def get_metadata(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status,
                "interface": "DataProvider/v0.7"}

    def health_check(self) -> dict[str, Any]:
        return {"status": self.status, "provider": self.name}

    def get_rate_limit(self) -> dict[str, Any]:
        return {"provider": self.name, "policy": "provider_defined"}

    @abstractmethod
    def get_data(self, request: dict[str, Any]) -> Any:
        """返回原始数据；必须保留 source/available_at 元数据。"""
        ...

    def get_coverage(self) -> dict[str, Any]:
        return {"provider": self.name, "coverage": "NOT_TESTED"}
