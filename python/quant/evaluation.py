"""评估与不可变预测记录。

预测记录写入后计算 SHA-256 并存入 SQLite，之后任何修改都会被哈希校验发现。
"""
from __future__ import annotations

# v0.2.0：统一使用 HMAC 链增强版；旧类保留为 LegacyPredictionStore 供对照
from .evaluation_v02 import PredictionStoreV2 as PredictionStore  # noqa: E402,F811

import hashlib
import json
from datetime import datetime
from typing import Any

import numpy as np
import pyarrow as pa

from .contracts import PredictionSpec
from .storage import MetadataDB
from .utils import ROOT, resolve, get_config
from .logging_util import log


def _ranks(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a)
    ranks = np.empty(len(a), dtype=float)
    ranks[order] = np.arange(len(a), dtype=float)
    return ranks


def evaluate(pred: np.ndarray, actual: np.ndarray, pred_rank: np.ndarray | None = None,
             actual_rank: np.ndarray | None = None) -> dict[str, Any]:
    pred = np.asarray(pred, dtype=float)
    actual = np.asarray(actual, dtype=float)
    mask = ~np.isnan(actual) & ~np.isnan(pred)
    if mask.sum() == 0:
        return {"n": 0, "note": "no valid pairs"}
    p, a = pred[mask], actual[mask]
    mae = float(np.mean(np.abs(p - a)))
    rmse = float(np.sqrt(np.mean((p - a) ** 2)))
    direction = float(np.mean(np.sign(p) == np.sign(a)))
    out: dict[str, Any] = {"n": int(mask.sum()), "mae": mae, "rmse": rmse,
                           "direction_accuracy": direction}
    if pred_rank is not None and actual_rank is not None:
        pr, ar = np.asarray(pred_rank)[mask], np.asarray(actual_rank)[mask]
        if np.std(pr) > 0 and np.std(ar) > 0:
            out["rank_ic"] = float(np.corrcoef(pr, ar)[0, 1])
            prk, ark = _ranks(pr), _ranks(ar)
            out["spearman"] = float(np.corrcoef(prk, ark)[0, 1])
    return out


class LegacyPredictionStore:
    def __init__(self):
        cfg = get_config()
        self.dir = resolve(cfg["storage"]["predictions_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = MetadataDB()

    @staticmethod
    def canonical_hash(obj: dict[str, Any]) -> str:
        # 排除 hash 字段本身，保证写入前后可重复计算
        payload = {k: v for k, v in obj.items() if k != "hash"}
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def save(self, spec: PredictionSpec, result: dict[str, Any]) -> dict[str, Any]:
        """写入不可变预测：先算哈希，再写文件，再存 SQLite。"""
        record = {
            "prediction_year": spec.prediction_year,
            "spec": spec.to_dict(),
            "result": result,
            "created_at": datetime.utcnow().isoformat(),
            "note": "sealed prediction; actual results must not be used to modify this record",
        }
        h = self.canonical_hash(record)
        record["hash"] = h
        pred_id = f"pred_{spec.prediction_year}_{h[:12]}"
        path = self.dir / f"{pred_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("hash") != h:
                raise RuntimeError("existing prediction record hash mismatch")
            return existing
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
                        encoding="utf-8")
        # 文件落盘后再计算一次哈希验证
        disk_hash = self.canonical_hash(json.loads(path.read_text(encoding="utf-8")))
        if disk_hash != h:
            path.unlink()
            raise RuntimeError("prediction file corrupted on write")
        self.meta.execute(
            "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?)",
            (pred_id, spec.prediction_year, json.dumps(spec.to_dict(), ensure_ascii=False),
             json.dumps(result, ensure_ascii=False), h,
             datetime.utcnow().isoformat(), 1),
        )
        log.info("预测记录冻结: %s hash=%s", pred_id, h[:16])
        return record

    def load(self, prediction_year: int) -> list[dict[str, Any]]:
        rows = self.meta.query("SELECT * FROM predictions WHERE prediction_year=?",
                               (prediction_year,))
        out = []
        for r in rows:
            p = self.dir / f"{r['pred_id']}.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            if self.canonical_hash(data) != r["result_hash"] or data.get("hash") != r["result_hash"]:
                raise RuntimeError(f"prediction tampered: {r['pred_id']}")
            out.append(data)
        return out

    def verify_all(self) -> dict[str, Any]:
        rows = self.meta.query("SELECT * FROM predictions")
        bad = []
        for r in rows:
            p = self.dir / f"{r['pred_id']}.json"
            if not p.exists():
                bad.append(r["pred_id"])
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if self.canonical_hash(data) != r["result_hash"] or data.get("hash") != r["result_hash"]:
                bad.append(r["pred_id"])
        return {"records": len(rows), "tampered": bad, "ok": not bad}
