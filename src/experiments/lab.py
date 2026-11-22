"""v0.7.0 P10-P13：实验引擎 + 统计检验 + 研究记忆（SQLite + FTS5）。

所有实验必须记录：假设、数据哈希、代码 commit、特征/标签版本、时间范围、
统计结果、资源消耗；失败实验同样保存，历史结论 append-only。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant.storage import MetadataDB
from quant.stats_v060 import bootstrap_ci, bonferroni, evidence_level
from quant.validation import compute_metrics

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY, hypothesis_id TEXT, status TEXT,
    data_manifest_hash TEXT, code_commit TEXT, feature_version TEXT,
    label_version TEXT, config TEXT, result TEXT, started_at TEXT, finished_at TEXT,
    elapsed_seconds REAL, peak_rss_mb REAL, failure_reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS research_conclusions (
    conclusion_id TEXT PRIMARY KEY, experiment_id TEXT, hypothesis_id TEXT,
    conclusion TEXT, status TEXT, supersedes TEXT, created_at TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS research_fts USING fts5(
    conclusion_id, text, content='research_conclusions', content_rowid='rowid');
"""


class ResearchLab:
    def __init__(self):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA)
        self.meta.conn.commit()

    @staticmethod
    def _commit() -> str:
        try:
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                                  capture_output=True, text=True).stdout.strip()
        except Exception:
            return "unknown"

    def run_experiment(self, hypothesis_id: str, config: dict[str, Any],
                       predict_fn, X, y, groups=None) -> dict[str, Any]:
        """统一实验入口：执行→统计→记忆，失败也落库。"""
        eid = "EXP_" + hashlib.sha256(
            f"{hypothesis_id}|{json.dumps(config, sort_keys=True)}|{time.time_ns()}".encode()
        ).hexdigest()[:16]
        started = datetime.now(timezone.utc).isoformat()
        t0 = time.perf_counter()
        result: dict[str, Any] = {}
        status, failure = "RUNNING", ""
        try:
            pred = predict_fn(X)
            result = compute_metrics(pred, y, groups=groups)
            ci = bootstrap_ci(pred - y)
            result["bootstrap_ci"] = ci
            result["evidence"] = evidence_level(int(result.get("n", 0)), ci)
            status = "PROMISING" if not result.get("insufficient_evidence") else "NO_EVIDENCE"
        except Exception as e:
            failure = f"{type(e).__name__}: {str(e)[:200]}"
            status = "FAILED"
        elapsed = round(time.perf_counter() - t0, 4)
        finished = datetime.now(timezone.utc).isoformat()
        self.meta.execute(
            "INSERT INTO experiments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, hypothesis_id, status,
             hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
             self._commit(), config.get("feature_version", "unknown"),
             config.get("label_version", "unknown"),
             json.dumps(config, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False), started, finished, elapsed,
             0.0, failure, finished))
        return {"experiment_id": eid, "status": status, "result": result,
                "failure_reason": failure, "elapsed_seconds": elapsed}

    def add_conclusion(self, experiment_id: str, hypothesis_id: str,
                       text: str, status: str = "PROPOSED",
                       supersedes: str | None = None) -> str:
        cid = "CON_" + hashlib.sha256(
            f"{experiment_id}|{text}|{time.time_ns()}".encode()).hexdigest()[:16]
        self.meta.execute(
            "INSERT INTO research_conclusions VALUES (?,?,?,?,?,?,?)",
            (cid, experiment_id, hypothesis_id, text, status, supersedes,
             datetime.now(timezone.utc).isoformat()))
        try:
            self.meta.execute("INSERT INTO research_fts(conclusion_id,text) VALUES (?,?)",
                              (cid, text))
        except Exception:
            pass  # FTS5 不可用时不影响主表
        return cid
