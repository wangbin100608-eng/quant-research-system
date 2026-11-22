"""v0.6.0 P1：强制时间门禁执行层（Agent/Embedding/模型/预测冻结）。

旧 provenance_v05/prediction_freeze_v05 只记录元数据；本模块在访问发生时**强制校验**：
  * Agent 工具调用必须提供 snapshot_id + cutoff，文档 available_at 必须 <= cutoff；
    越权请求被阻断并持久化日志。
  * Embedding 注册必须含 doc_sha256/model_file_hash/config/cutoff/kb_version/vector_snapshot_hash；
    历史查询只返回 cutoff 前生成的向量。
  * 模型注册必须含 selection_basis + final_test_range，验证选择未使用未来测试集。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant.storage import MetadataDB

SCHEMA = """
CREATE TABLE IF NOT EXISTS enforce_agent_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, action TEXT,
    snapshot_id TEXT, cutoff TEXT, doc_ids TEXT, allowed INTEGER,
    reason TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS enforce_vectors (
    vector_id TEXT PRIMARY KEY, doc_id TEXT, doc_sha256 TEXT,
    embedding_model TEXT, model_file_hash TEXT, config TEXT,
    kb_version TEXT, snapshot_id TEXT, cutoff TEXT, generated_at TEXT,
    vector_snapshot_hash TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS enforce_models (
    model_id TEXT PRIMARY KEY, snapshot_id TEXT, train_max_available_at TEXT,
    train_cutoff TEXT, selection_basis TEXT, final_test_range TEXT,
    hyperparam_space TEXT, created_at TEXT
);
"""


class TimeEnforcementV060:
    def __init__(self):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA)
        self.meta.conn.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---------- Agent 强制门禁 ----------
    def agent_call(self, agent: str, action: str, snapshot_id: str | None,
                   cutoff: datetime | None, doc_available_at: dict[str, str] | None = None,
                   tool: str | None = None) -> tuple[bool, str]:
        reason = ""
        allowed = True
        if not snapshot_id:
            allowed, reason = False, "missing_snapshot_id"
        elif cutoff is None or cutoff.tzinfo is None:
            allowed, reason = False, "missing_or_naive_cutoff"
        else:
            for doc_id, av in (doc_available_at or {}).items():
                try:
                    dt = datetime.fromisoformat(str(av).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                except Exception:
                    allowed, reason = False, f"invalid_available_at:{doc_id}"
                    break
                if dt > cutoff:
                    allowed, reason = False, f"future_document:{doc_id}"
                    break
        self.meta.execute(
            "INSERT INTO enforce_agent_calls(agent,action,snapshot_id,cutoff,doc_ids,allowed,reason,ts) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (agent, action, snapshot_id, cutoff.isoformat() if cutoff else None,
             json.dumps(list((doc_available_at or {}).keys())), 1 if allowed else 0,
             reason, self._now()),
        )
        return allowed, reason

    def agent_violations(self, agent: str | None = None) -> list[dict[str, Any]]:
        if agent:
            return self.meta.query("SELECT * FROM enforce_agent_calls WHERE agent=? AND allowed=0",
                                   (agent,))
        return self.meta.query("SELECT * FROM enforce_agent_calls WHERE allowed=0")

    # ---------- Embedding 强制溯源 ----------
    def register_vector(self, vector_id: str, doc_id: str, doc_path: str,
                        embedding_model: str, model_file: str, config: dict,
                        kb_version: str, snapshot_id: str, cutoff: datetime,
                        vector_snapshot_hash: str,
                        generated_at: datetime | None = None) -> None:
        def sha(p: str) -> str:
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 16), b""):
                    h.update(chunk)
            return h.hexdigest()
        self.meta.execute(
            "INSERT OR REPLACE INTO enforce_vectors VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (vector_id, doc_id, sha(doc_path), embedding_model, sha(model_file),
             json.dumps(config, sort_keys=True), kb_version, snapshot_id,
             cutoff.isoformat(), (generated_at or datetime.now(timezone.utc)).isoformat(),
             vector_snapshot_hash, self._now()))

    def vector_visible(self, vector_id: str, snapshot_id: str, cutoff: datetime) -> bool:
        rows = self.meta.query("SELECT * FROM enforce_vectors WHERE vector_id=? AND snapshot_id=?",
                               (vector_id, snapshot_id))
        if not rows:
            return False  # 跨快照不可见
        r = rows[0]
        gen = datetime.fromisoformat(r["generated_at"].replace("Z", "+00:00"))
        vec_cut = datetime.fromisoformat(r["cutoff"].replace("Z", "+00:00"))
        return gen <= cutoff and vec_cut <= cutoff

    # ---------- 模型选择防泄漏 ----------
    def register_model(self, model_id: str, snapshot_id: str,
                       train_max_available_at: str, train_cutoff: datetime,
                       selection_basis: str, final_test_range: list[str],
                       hyperparam_space: dict) -> tuple[bool, str]:
        # 选择依据不得引用最终测试集结果（简单字符串/字段检查）
        forbidden = [t for t in final_test_range if t and t in str(selection_basis)]
        if forbidden:
            return False, f"selection_uses_final_test:{forbidden}"
        self.meta.execute(
            "INSERT OR REPLACE INTO enforce_models VALUES (?,?,?,?,?,?,?,?)",
            (model_id, snapshot_id, train_max_available_at, train_cutoff.isoformat(),
             selection_basis, json.dumps(final_test_range),
             json.dumps(hyperparam_space, sort_keys=True), self._now()))
        return True, ""
