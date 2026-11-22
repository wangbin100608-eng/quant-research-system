"""v0.5.0 P1：Embedding / 模型权重 / Agent 时间溯源与越权审计。

不修改已有数据；新增审计表与查询包装：
  * vector_provenance：每个 embedding 记录 doc_id / 模型名版本 / 生成时间 / 源文档 max available_at / snapshot_id / 向量库版本。
  * model_provenance：训练快照 / max available_at / 归一化参数 / 缺失填充 / 特征选择 / 超参范围 / 验证范围 / 种子 / 环境 / 权重哈希。
  * agent_access_log：Agent 工具调用 / 知识库检索 / 接触文档 ID / 数据快照 / cutoff（持久化）。
所有查询强制 cutoff + snapshot_id；未来数据不可见；越权访问被记录并阻断。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from quant.storage import MetadataDB
from quant.timestamps_v04 import cutoff_for_year

SCHEMA = """
CREATE TABLE IF NOT EXISTS vector_provenance (
    vector_id TEXT PRIMARY KEY, doc_id TEXT, embedding_model TEXT, model_version TEXT,
    generated_at TEXT, source_max_available_at TEXT, snapshot_id TEXT,
    vector_db_version TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS model_provenance (
    model_id TEXT PRIMARY KEY, train_snapshot_id TEXT, train_max_available_at TEXT,
    train_cutoff TEXT, feature_version TEXT, normalization TEXT, imputation TEXT,
    feature_selection TEXT, hyperparam_space TEXT, validation_range TEXT,
    random_seed TEXT, environment TEXT, weight_hash TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS agent_access_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, action TEXT, tool TEXT,
    doc_ids TEXT, snapshot_id TEXT, cutoff TEXT, allowed INTEGER, detail TEXT, ts TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProvenanceV05:
    def __init__(self):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA)
        self.meta.conn.commit()

    # ---------- Embedding ----------
    def register_vector(self, vector_id: str, doc_id: str, embedding_model: str,
                        model_version: str, source_max_available_at: str,
                        snapshot_id: str, vector_db_version: str = "sqlite-v1") -> None:
        self.meta.execute(
            "INSERT OR REPLACE INTO vector_provenance VALUES (?,?,?,?,?,?,?,?,?)",
            (vector_id, doc_id, embedding_model, model_version, _now(),
             source_max_available_at, snapshot_id, vector_db_version, _now()))

    def vector_visible(self, vector_id: str, snapshot_id: str, cutoff: datetime) -> bool:
        rows = self.meta.query("SELECT * FROM vector_provenance WHERE vector_id=? AND snapshot_id=?",
                               (vector_id, snapshot_id))
        if not rows:
            return False
        av = rows[0]["source_max_available_at"]
        return bool(av and datetime.fromisoformat(av.replace("Z", "+00:00")) <= cutoff)

    # ---------- 模型 ----------
    def register_model(self, model_id: str, train_snapshot_id: str,
                       train_max_available_at: str, train_cutoff: str,
                       feature_version: str, normalization: dict, imputation: dict,
                       feature_selection: list, hyperparam_space: dict,
                       validation_range: list, random_seed: str, environment: dict,
                       weight_path: str) -> None:
        h = hashlib.sha256()
        with open(weight_path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        self.meta.execute(
            "INSERT OR REPLACE INTO model_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (model_id, train_snapshot_id, train_max_available_at, train_cutoff,
             feature_version, json.dumps(normalization), json.dumps(imputation),
             json.dumps(feature_selection), json.dumps(hyperparam_space),
             json.dumps(validation_range), random_seed, json.dumps(environment),
             h.hexdigest(), _now()))

    def model_visible(self, model_id: str, cutoff: datetime) -> bool:
        rows = self.meta.query("SELECT * FROM model_provenance WHERE model_id=?", (model_id,))
        if not rows:
            return False
        av = rows[0]["train_max_available_at"]
        return bool(av and datetime.fromisoformat(av.replace("Z", "+00:00")) <= cutoff)

    # ---------- Agent ----------
    def log_access(self, agent: str, action: str, snapshot_id: str, cutoff: str,
                   doc_ids: list[str] | None = None, tool: str | None = None,
                   allowed: bool = True, detail: str = "") -> None:
        self.meta.execute(
            "INSERT INTO agent_access_log(agent,action,tool,doc_ids,snapshot_id,cutoff,allowed,detail,ts) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (agent, action, tool, json.dumps(doc_ids or []), snapshot_id, cutoff,
             1 if allowed else 0, detail, _now()))

    def agent_violations(self, agent: str | None = None) -> list[dict[str, Any]]:
        if agent:
            return self.meta.query("SELECT * FROM agent_access_log WHERE agent=? AND allowed=0", (agent,))
        return self.meta.query("SELECT * FROM agent_access_log WHERE allowed=0")
