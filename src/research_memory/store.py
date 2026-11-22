"""v0.7 P13/P10：Research Memory 9 张表 + 数据血缘。

建立完整表结构（已有表用 CREATE IF NOT EXISTS，不改历史数据），
并为每次实验保存 data_lineage（输入 manifest 哈希 + 时间范围 + 代码 commit）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from quant.storage import MetadataDB

SCHEMA_V07 = """
CREATE TABLE IF NOT EXISTS hypotheses_v07 (
    hypothesis_id TEXT PRIMARY KEY, created_at TEXT, cutoff_date TEXT, content TEXT,
    pre_registration TEXT, status TEXT, research_family_id TEXT, snapshot_id TEXT
);
CREATE TABLE IF NOT EXISTS experiments_v07 (
    experiment_id TEXT PRIMARY KEY, hypothesis_id TEXT, status TEXT, config TEXT,
    data_manifest_hash TEXT, feature_version TEXT, label_version TEXT,
    code_commit TEXT, started_at TEXT, finished_at TEXT, elapsed_seconds REAL,
    peak_rss_mb REAL, failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS experiment_results (
    result_id TEXT PRIMARY KEY, experiment_id TEXT, metrics TEXT, ci TEXT,
    adjusted_pvalues TEXT, evidence_level TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS research_conclusions_v07 (
    conclusion_id TEXT PRIMARY KEY, experiment_id TEXT, hypothesis_id TEXT,
    conclusion TEXT, status TEXT, supersedes TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS experiment_relationships (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id TEXT, child_id TEXT,
    relation_type TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS feature_registry_v07 (
    feature_id TEXT PRIMARY KEY, feature_name TEXT, category TEXT, spec TEXT,
    version TEXT, code_hash TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS data_manifests_v07 (
    manifest_id TEXT PRIMARY KEY, market TEXT, provider TEXT, dataset TEXT,
    source_sha256 TEXT, time_min TEXT, time_max TEXT, rows INTEGER,
    synthetic INTEGER, estimated_time_rows INTEGER, created_at TEXT
);
CREATE TABLE IF NOT EXISTS model_registry_v07 (
    model_id TEXT PRIMARY KEY, group_key TEXT, version TEXT, params TEXT,
    weight_hash TEXT, train_snapshot_id TEXT, train_max_available_at TEXT,
    selection_basis TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS prediction_records_v07 (
    prediction_id TEXT PRIMARY KEY, market TEXT, horizon TEXT, prediction_time TEXT,
    symbol TEXT, output TEXT, output_hash TEXT, model_version TEXT,
    feature_version TEXT, data_manifest_hash TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS data_lineage (
    lineage_id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT,
    manifest_hash TEXT, time_min TEXT, time_max TEXT, code_commit TEXT,
    feature_version TEXT, label_version TEXT, created_at TEXT
);
"""


class ResearchMemoryV07:
    """9 张核心表 + data_lineage 血缘表。"""

    TABLES = ["hypotheses_v07", "experiments_v07", "experiment_results",
              "research_conclusions_v07", "experiment_relationships",
              "feature_registry_v07", "data_manifests_v07",
              "model_registry_v07", "prediction_records_v07"]

    def __init__(self):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA_V07)
        self.meta.conn.commit()

    def tables(self) -> list[str]:
        rows = self.meta.query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ("
            + ",".join("?" for _ in self.TABLES) + ")", self.TABLES)
        return sorted(r["name"] for r in rows)

    def record_lineage(self, experiment_id: str, manifest_hash: str,
                       time_min: str, time_max: str, code_commit: str,
                       feature_version: str, label_version: str) -> int:
        self.meta.execute(
            "INSERT INTO data_lineage(experiment_id,manifest_hash,time_min,time_max,"
            "code_commit,feature_version,label_version,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (experiment_id, manifest_hash, time_min, time_max, code_commit,
             feature_version, label_version,
             datetime.now(timezone.utc).isoformat()))
        return 1

    def lineage_for(self, experiment_id: str) -> list[dict[str, Any]]:
        return self.meta.query("SELECT * FROM data_lineage WHERE experiment_id=?",
                               (experiment_id,))
