"""v0.3.0 P1：六库统一知识系统 + 假设生命周期。

六库：
  1 structured  结构化知识（指标/实体属性）
  2 document    文档知识（新闻/公告/财报摘要）
  3 vector      向量知识（Embedding + 文本，按需）
  4 event       事件知识（事件时间 + 披露时间）
  5 hypothesis  研究假设库（完整字段 + 状态机 + 失败保留）
  6 rule        决策规则库（仅 validated 可生效）

统一元数据（每条必带）：
  source / published_time / available_time / version / snapshot_id

物理隔离：每条记录绑定 snapshot_id；查询强制传 cutoff_date，
只返回 available_time <= cutoff 且 snapshot_id 匹配的记录。严格模式拒绝 unknown。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .storage import MetadataDB
from .timeguard import LeakError, TimeTravelEngine
from .utils import get_config, resolve
from .logging_util import log


class HypothesisStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


SCHEMA_SQL_V03 = """
CREATE TABLE IF NOT EXISTS kb_items (
    item_id TEXT PRIMARY KEY,
    library TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    source TEXT,
    published_time TEXT,
    available_time TEXT,
    version TEXT,
    content TEXT,
    extra TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_lib_snap ON kb_items(library, snapshot_id, available_time);

CREATE TABLE IF NOT EXISTS hypotheses (
    hypothesis_id TEXT PRIMARY KEY,
    created_at TEXT,
    cutoff_date TEXT,
    content TEXT,
    pre_registration TEXT,
    model TEXT,
    train_sample TEXT,
    test_sample TEXT,
    validation_result TEXT,
    falsification TEXT,
    status TEXT,
    snapshot_id TEXT,
    history TEXT,
    updated_at TEXT
);
"""


class KnowledgeSystemV03:
    def __init__(self, strict: bool | None = None):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA_SQL_V03)
        self.meta.conn.commit()
        cfg = get_config()
        self.strict = cfg["research"]["strict_point_in_time"] if strict is None else strict
        self.tg = TimeTravelEngine()

    # ---------- 通用写入 ----------
    def add(self, library: str, item_id: str, snapshot_id: str,
            source: str, published_time: str | None, available_time: str | None,
            content: str | dict, version: str = "v0.3.0",
            extra: dict | None = None) -> str:
        if self.strict and not available_time:
            raise LeakError(f"unknown_availability: {library}/{item_id} 缺少 available_time")
        if self.strict and not source:
            raise LeakError(f"missing_source: {library}/{item_id}")
        c = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        self.meta.execute(
            "INSERT OR REPLACE INTO kb_items VALUES (?,?,?,?,?,?,?,?,?,?)",
            (item_id, library, snapshot_id, source, published_time, available_time,
             version, c, json.dumps(extra or {}, ensure_ascii=False),
             datetime.utcnow().isoformat()),
        )
        return item_id

    # ---------- 通用查询（强制时间边界）----------
    def query(self, library: str, cutoff_date: date, snapshot_id: str | None = None,
              limit: int = 1000) -> list[dict[str, Any]]:
        rows = self.meta.query(
            "SELECT * FROM kb_items WHERE library=? AND available_time<=? "
            "ORDER BY available_time DESC LIMIT ?",
            (library, cutoff_date.isoformat(), limit),
        )
        out = []
        for r in rows:
            if snapshot_id and r["snapshot_id"] != snapshot_id:
                continue
            # 严格模式二次校验（防止 DB 被直接写入未来数据）
            try:
                self.tg.validate_row({
                    "symbol": r["item_id"], "available_time": r["available_time"],
                    "source": r["source"], "payload": r["extra"],
                }, _spec_for(cutoff_date))
            except LeakError:
                continue
            out.append(r)
        return out

    # ---------- 六库便捷方法 ----------
    def add_structured(self, item_id, snapshot_id, source, available_time, content, **kw):
        return self.add("structured", item_id, snapshot_id, source,
                        kw.get("published_time", available_time), available_time, content, **kw)

    def add_document(self, item_id, snapshot_id, source, published_time, available_time,
                     content, embedding: list[float] | None = None, **kw):
        extra = {"embedding": embedding} if embedding else {}
        extra.update(kw.get("extra", {}))
        kw["extra"] = extra
        return self.add("document", item_id, snapshot_id, source,
                        published_time, available_time, content, **kw)

    def add_vector(self, item_id, snapshot_id, source, available_time, text,
                   embedding: list[float], **kw):
        extra = {"text": text, "embedding": embedding, "dim": len(embedding)}
        return self.add("vector", item_id, snapshot_id, source,
                        kw.get("published_time", available_time), available_time,
                        {"text": text}, extra=extra, **kw)

    def add_event(self, item_id, snapshot_id, source, event_time, published_time,
                  available_time, description, **kw):
        extra = {"event_time": event_time}
        return self.add("event", item_id, snapshot_id, source,
                        published_time, available_time, description, extra=extra, **kw)

    def add_rule(self, rule_id, snapshot_id, content, **kw):
        # 未经样本外验证的规则不能升级 validated
        if kw.get("status", "pending") == HypothesisStatus.VALIDATED and not kw.get("oos_performance"):
            raise ValueError("未经样本外验证的规则不能升级为 validated")
        extra = {"status": kw.get("status", "pending"),
                 "market": kw.get("market", "ALL"),
                 "valid_from": kw.get("valid_from"),
                 "valid_to": kw.get("valid_to"),
                 "sample_size": kw.get("sample_size"),
                 "oos_performance": kw.get("oos_performance"),
                 "invalidation": kw.get("invalidation")}
        return self.add("rule", rule_id, snapshot_id, kw.get("source", "research"),
                        kw.get("published_time", "2000-01-01"),
                        kw.get("available_time", "2000-01-01"), content, extra=extra)

    # ---------- 假设管理（P1-3）----------
    def create_hypothesis(self, hypothesis_id: str, content: str, cutoff_date: date,
                          pre_registration: dict, model: str, snapshot_id: str) -> None:
        rec = {
            "hypothesis_id": hypothesis_id,
            "created_at": datetime.utcnow().isoformat(),
            "cutoff_date": cutoff_date.isoformat(),
            "content": content,
            "pre_registration": json.dumps(pre_registration, ensure_ascii=False),
            "model": model,
            "train_sample": None, "test_sample": None,
            "validation_result": None,
            "falsification": pre_registration.get("falsification"),
            "status": HypothesisStatus.PENDING.value,
            "snapshot_id": snapshot_id,
            "history": json.dumps([{"event": "created", "ts": datetime.utcnow().isoformat()}]),
            "updated_at": datetime.utcnow().isoformat(),
        }
        self.meta.execute(
            "INSERT OR REPLACE INTO hypotheses VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            tuple(rec.values()))

    def update_hypothesis(self, hypothesis_id: str, status: HypothesisStatus,
                          train_sample: dict | None = None, test_sample: dict | None = None,
                          validation_result: dict | None = None) -> None:
        rows = self.meta.query("SELECT * FROM hypotheses WHERE hypothesis_id=?", (hypothesis_id,))
        if not rows:
            raise KeyError(hypothesis_id)
        r = rows[0]
        hist = json.loads(r["history"] or "[]")
        hist.append({"event": f"status->{status.value}", "ts": datetime.utcnow().isoformat(),
                     "validation": validation_result})
        # 状态机约束：validated 必须有样本外结果
        if status == HypothesisStatus.VALIDATED and not validation_result:
            raise ValueError("validated 假设必须提供服务样本外验证结果")
        self.meta.execute(
            "UPDATE hypotheses SET status=?, train_sample=?, test_sample=?, "
            "validation_result=?, history=?, updated_at=? WHERE hypothesis_id=?",
            (status.value,
             json.dumps(train_sample, ensure_ascii=False) if train_sample else r["train_sample"],
             json.dumps(test_sample, ensure_ascii=False) if test_sample else r["test_sample"],
             json.dumps(validation_result, ensure_ascii=False) if validation_result else r["validation_result"],
             json.dumps(hist, ensure_ascii=False), datetime.utcnow().isoformat(),
             hypothesis_id),
        )

    def list_hypotheses(self, status: HypothesisStatus | None = None) -> list[dict[str, Any]]:
        if status:
            return self.meta.query("SELECT * FROM hypotheses WHERE status=?", (status.value,))
        return self.meta.query("SELECT * FROM hypotheses")


def _spec_for(cutoff_date: date):
    from .contracts import PredictionSpec
    return PredictionSpec.for_year(cutoff_date.year + 1, 2000,
                                   f"asof_{cutoff_date.year}", "kb_query", "v0.3.0")
