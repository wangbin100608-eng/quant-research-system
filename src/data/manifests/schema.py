"""v0.7.0 P3：数据 manifest 契约（原始不可变存储）。"""
from __future__ import annotations

MANIFEST_SCHEMA = (
    "manifest_id", "market", "provider", "dataset", "date",
    "source_sha256", "source_url", "ingested_at", "available_at_max",
    "rows", "synthetic", "estimated_time_rows", "status",
)
