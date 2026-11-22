"""公开数据适配器基类：统一元数据、五时间字段、断点续传钩子。"""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..contracts import REQUIRED_TIME_FIELDS
from ..utils import ROOT, resolve


CANONICAL_SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("market", pa.string()),
    ("domain", pa.string()),
    ("event_time", pa.string()),
    ("published_time", pa.string()),
    ("available_time", pa.string()),
    ("collected_time", pa.string()),
    ("revision_time", pa.string()),
    ("source", pa.string()),
    ("payload", pa.string()),   # JSON 编码的业务字段
    ("fetched_at", pa.string()),
])


@dataclass
class FetchResult:
    source: str
    mode: str
    table: pa.Table
    coverage: dict[str, Any] = field(default_factory=dict)
    missing_ranges: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_parquet(self, rel_path: str) -> Path:
        out = resolve(rel_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        import pyarrow.parquet as pq
        pq.write_table(self.table, str(out), compression="zstd")
        return out


class BaseAdapter(ABC):
    name = "base"

    def __init__(self, mode: str = "auto", cfg: dict | None = None):
        self.mode = mode
        self.cfg = cfg or {}

    @abstractmethod
    def fetch(self) -> FetchResult:
        ...

    @staticmethod
    def make_row(symbol: str, market: str, domain: str, source: str,
                 event: date | None, published: date | None,
                 available: date | None, payload: dict[str, Any],
                 revision: date | None = None,
                 collected: date | None = None) -> dict[str, Any]:
        """所有适配器必须通过此函数产出行，保证五时间字段存在。"""
        if collected is None:
            collected = date.today()
        return {
            "symbol": symbol,
            "market": market,
            "domain": domain,
            "event_time": event.isoformat() if event else None,  # None => unknown
            "published_time": published.isoformat() if published else None,
            "available_time": available.isoformat() if available else None,
            "collected_time": collected.isoformat(),
            "revision_time": revision.isoformat() if revision else None,
            "source": source,
            "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "fetched_at": datetime.utcnow().isoformat(),
        }

    @staticmethod
    def to_table(rows: list[dict[str, Any]]) -> pa.Table:
        return pa.Table.from_pylist(rows, schema=CANONICAL_SCHEMA)

    @staticmethod
    def fingerprint(table: pa.Table) -> str:
        payload = "\n".join(table.column("payload").to_pylist())
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
