"""v0.5.0 P0：预测冻结完整溯源（prediction_id/commit/快照/训练上限/cutoff/frozen_at/修正链）。

在 v02 HMAC 链基础上补齐溯源字段；不修改历史预测，新增记录写独立表 prediction_freeze_v05。
支持：save_freeze / verify_freeze / amend(修正生成新版本+原因) / CLI verify。
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.storage import MetadataDB
from quant.utils import resolve
from quant.evaluation_v02 import PredictionStoreV2, canonical_bytes

SCHEMA = """
CREATE TABLE IF NOT EXISTS prediction_freeze_v05 (
    freeze_id TEXT PRIMARY KEY,
    prediction_id TEXT,
    prediction_year INTEGER,
    data_snapshot_id TEXT,
    model_version TEXT,
    feature_version TEXT,
    code_commit TEXT,
    training_max_available_at TEXT,
    prediction_cutoff TEXT,
    frozen_at TEXT,
    prediction_hash TEXT,
    hmac_sig TEXT,
    prev_sig TEXT,
    amend_reason TEXT,
    is_amendment INTEGER DEFAULT 0,
    status TEXT DEFAULT 'frozen'
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def _key() -> bytes:
    p = resolve("data/.seal_key")
    if p.exists():
        return p.read_text().strip().encode()
    return b"v05-fallback"


class FreezeV05:
    def __init__(self):
        self.meta = MetadataDB()
        self.meta.conn.executescript(SCHEMA)
        self.meta.conn.commit()
        self.key = _key()

    def _sign(self, freeze_id: str, record: dict, prev: str) -> str:
        msg = prev.encode() + canonical_bytes(record)
        return hmac.new(self.key, msg, hashlib.sha256).hexdigest()

    def save_freeze(self, prediction_id: str, prediction_year: int,
                    data_snapshot_id: str, model_version: str, feature_version: str,
                    training_max_available_at: str, prediction_cutoff: str,
                    prediction_result: dict, amend_reason: str | None = None) -> dict[str, Any]:
        rows = self.meta.query("SELECT * FROM prediction_freeze_v05 ORDER BY frozen_at DESC LIMIT 1")
        prev = rows[0]["hmac_sig"] if rows else "0" * 64
        is_amend = 1 if amend_reason else 0
        freeze_id = f"freeze_{prediction_id}_{len(rows)+1:03d}"
        rec = {
            "freeze_id": freeze_id, "prediction_id": prediction_id,
            "prediction_year": prediction_year, "data_snapshot_id": data_snapshot_id,
            "model_version": model_version, "feature_version": feature_version,
            "code_commit": _commit(),
            "training_max_available_at": training_max_available_at,
            "prediction_cutoff": prediction_cutoff, "frozen_at": _now(),
            "prediction_hash": hashlib.sha256(
                json.dumps(prediction_result, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "amend_reason": amend_reason, "is_amendment": is_amend,
        }
        sig = self._sign(freeze_id, rec, prev)
        rec["prev_sig"] = prev
        rec["hmac_sig"] = sig
        cols = ["freeze_id", "prediction_id", "prediction_year", "data_snapshot_id",
                "model_version", "feature_version", "code_commit",
                "training_max_available_at", "prediction_cutoff", "frozen_at",
                "prediction_hash", "hmac_sig", "prev_sig", "amend_reason",
                "is_amendment"]
        self.meta.execute(
            f"INSERT INTO prediction_freeze_v05 ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})",
            tuple(rec[c] for c in cols))
        # 冻结后禁止覆盖/删除（触发器）
        self.meta.execute("""CREATE TRIGGER IF NOT EXISTS freeze_v05_no_update
            BEFORE UPDATE ON prediction_freeze_v05 BEGIN
            SELECT RAISE(ABORT,'frozen prediction cannot be modified'); END""")
        self.meta.execute("""CREATE TRIGGER IF NOT EXISTS freeze_v05_no_delete
            BEFORE DELETE ON prediction_freeze_v05 BEGIN
            SELECT RAISE(ABORT,'frozen prediction cannot be deleted'); END""")
        return rec

    def verify_freeze(self) -> dict[str, Any]:
        rows = self.meta.query("SELECT * FROM prediction_freeze_v05 ORDER BY frozen_at")
        bad = []
        prev = "0" * 64
        excluded = {"hmac_sig", "prev_sig", "status"}
        for r in rows:
            rec = {k: v for k, v in r.items() if k not in excluded}
            # canonical_bytes 还会排除 hash/chain_prev/chain_index（本表无这些列）
            if r["prev_sig"] != prev or not hmac.compare_digest(
                    self._sign(r["freeze_id"], rec, prev), r["hmac_sig"]):
                bad.append(r["freeze_id"])
            prev = r["hmac_sig"]
        return {"records": len(rows), "tampered": bad, "ok": not bad}

    def amend(self, *args, reason: str, **kwargs) -> dict[str, Any]:
        if not reason:
            raise ValueError("修正预测必须提供 amend_reason")
        return self.save_freeze(*args, amend_reason=reason, **kwargs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["verify"])
    args = ap.parse_args()
    if args.action == "verify":
        r = FreezeV05().verify_freeze()
        print(json.dumps(r, ensure_ascii=False, indent=2))
        sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
