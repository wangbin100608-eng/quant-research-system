"""存储层：SQLite 元数据 + DuckDB 分析 + Parquet 分区读写（列式/惰性）。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .utils import ROOT, resolve, get_config


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    asof_year INTEGER NOT NULL,
    cutoff_date TEXT NOT NULL,
    created_at TEXT NOT NULL,
    entry_count INTEGER DEFAULT 0,
    file_hash TEXT,
    model_version TEXT,
    feature_version TEXT,
    sealed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS predictions (
    pred_id TEXT PRIMARY KEY,
    prediction_year INTEGER NOT NULL,
    spec_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    year INTEGER,
    step TEXT,
    status TEXT,
    detail TEXT,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_runs (
    run_id TEXT PRIMARY KEY,
    source TEXT,
    mode TEXT,
    started_at TEXT,
    finished_at TEXT,
    rows INTEGER,
    status TEXT,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task TEXT,
    elapsed_seconds REAL,
    mem_peak_mb REAL,
    rss_mb REAL,
    rss_delta_mb REAL,
    disk_bytes INTEGER,
    ts TEXT
);
CREATE TABLE IF NOT EXISTS decision_rules (
    rule_id TEXT PRIMARY KEY,
    content TEXT,
    market TEXT,
    valid_from TEXT,
    valid_to TEXT,
    source TEXT,
    method TEXT,
    sample_size INTEGER,
    oos_performance TEXT,
    invalidation TEXT,
    status TEXT,
    created_at TEXT
);
"""


class MetadataDB:
    def __init__(self, path: str | None = None):
        cfg = get_config()
        self.path = resolve(path or cfg["storage"]["metadata_db"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL)
        # v0.2.0 幂等迁移：旧库补新增列
        existing = {r["name"] for r in self.conn.execute("PRAGMA table_info(metrics)")}
        for col, ddl in (("rss_mb", "REAL"), ("rss_delta_mb", "REAL")):
            if col not in existing:
                self.conn.execute(f"ALTER TABLE metrics ADD COLUMN {col} {ddl}")
        self.conn.commit()

    def execute(self, sql: str, params: Iterable = ()) -> sqlite3.Cursor:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cur

    def query(self, sql: str, params: Iterable = ()) -> list[dict[str, Any]]:
        cur = self.conn.execute(sql, tuple(params))
        return [dict(r) for r in cur.fetchall()]

    def log_task(self, task_id: str, year: int | None, step: str,
                 status: str, detail: str = "") -> None:
        self.execute(
            "INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?)",
            (task_id, year, step, status, detail, datetime.utcnow().isoformat()),
        )

    def save_metric(self, stats: dict[str, Any], disk_bytes: int = 0) -> None:
        self.execute(
            "INSERT INTO metrics(task,elapsed_seconds,mem_peak_mb,rss_mb,rss_delta_mb,disk_bytes,ts) "
            "VALUES(?,?,?,?,?,?,?)",
            (stats.get("task"), stats.get("elapsed_seconds"),
             stats.get("mem_peak_mb"), stats.get("rss_mb"),
             stats.get("rss_delta_mb"), disk_bytes, datetime.utcnow().isoformat()),
        )

    def close(self):
        self.conn.close()


def write_parquet(table: pa.Table, rel_path: str, partition_cols: list[str] | None = None) -> Path:
    """列式写入；支持按字段分区，避免单文件过大。"""
    out = resolve(rel_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if partition_cols:
        pq.write_to_dataset(table, root_path=str(out), partition_cols=partition_cols,
                            compression="zstd", use_dictionary=True)
    else:
        pq.write_table(table, str(out), compression="zstd")
    return out


def scan_parquet(rel_path: str, columns: list[str] | None = None,
                 filters: Any = None) -> pa.Table:
    """列投影 + 行组过滤，不全量载入。"""
    path = resolve(rel_path)
    return pq.read_table(str(path), columns=columns, filters=filters)


class DuckDBStore:
    def __init__(self, path: str | None = None):
        cfg = get_config()
        self.path = resolve(path or cfg["storage"]["analytics_db"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))

    def sql(self, query: str, params: list | None = None):
        return self.conn.execute(query, params or []).fetchall()

    def register_parquet(self, name: str, rel_path: str) -> None:
        p = resolve(rel_path)
        self.conn.execute(
            f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{p}', union_by_name=true)"
        )

    def close(self):
        self.conn.close()
