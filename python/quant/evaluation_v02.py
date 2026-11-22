"""v0.2.0 预测记录不可变性升级：HMAC-SHA256 + 链哈希 + append-only 触发器。

修复 AUDIT_REPORT.md P0：v0.1 无密钥 SHA-256 且校验基准与数据同处可写 SQLite，
双端同步篡改可绕过。v0.2：
  * 每条记录签名 = HMAC(key, prev_sig || canonical(record))，密钥独立于数据与 DB；
  * 链头固定（genesis），verify_all 校验链连续性，检测删行/插队/重排；
  * SQLite 对 predictions 建立 BEFORE UPDATE/DELETE 触发器，禁止改写；
  * 原子落盘（临时文件 + os.replace + fsync）；
  * 消除双份真相源：verify_all 比对 DB 中 spec_json/result_json 与文件内容一致。
密钥位置：环境变量 QUANT_SEAL_KEY 或 data/.seal_key（首次自动生成，chmod 600）。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import MetadataDB
from .utils import ROOT, resolve, get_config
from .logging_util import log

GENESIS = "0" * 64


def _load_key() -> bytes:
    env = os.environ.get("QUANT_SEAL_KEY")
    if env:
        return env.encode("utf-8")
    key_path = resolve("data/.seal_key")
    if not key_path.exists():
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key = os.urandom(32).hex()
        key_path.write_text(key, encoding="utf-8")
        os.chmod(key_path, 0o600)
        return key.encode("utf-8")
    return key_path.read_text(encoding="utf-8").strip().encode("utf-8")


def canonical_bytes(obj: dict[str, Any]) -> bytes:
    payload = {k: v for k, v in obj.items()
               if k not in ("hash", "hmac_sig", "chain_prev", "chain_index")}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return raw.encode("utf-8")


def install_append_only_triggers(meta: MetadataDB) -> None:
    """SQLite 触发器：禁止 UPDATE/DELETE predictions（append-only）。"""
    meta.execute("""CREATE TRIGGER IF NOT EXISTS predictions_no_update
        BEFORE UPDATE ON predictions BEGIN
            SELECT RAISE(ABORT, 'predictions are append-only');
        END""")
    meta.execute("""CREATE TRIGGER IF NOT EXISTS predictions_no_delete
        BEFORE DELETE ON predictions BEGIN
            SELECT RAISE(ABORT, 'predictions are append-only');
        END""")


class PredictionStoreV2:
    def __init__(self):
        cfg = get_config()
        self.dir = resolve(cfg["storage"]["predictions_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta = MetadataDB()
        self.key = _load_key()
        install_append_only_triggers(self.meta)
        # v0.2 链元数据表（独立于 predictions，记录链头）
        self.meta.execute("""CREATE TABLE IF NOT EXISTS prediction_chain (
            chain_index INTEGER PRIMARY KEY,
            pred_id TEXT,
            signature TEXT,
            prev_signature TEXT,
            ts TEXT
        )""")

    @staticmethod
    def simple_hash(obj: dict[str, Any]) -> str:
        return hashlib.sha256(canonical_bytes(obj)).hexdigest()

    def _sign(self, record: dict[str, Any], prev_sig: str) -> str:
        return hmac.new(self.key, prev_sig.encode() + canonical_bytes(record),
                        hashlib.sha256).hexdigest()

    def _atomic_write(self, path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".pred_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def save(self, spec, result: dict[str, Any]) -> dict[str, Any]:
        # 链尾
        rows = self.meta.query("SELECT * FROM prediction_chain ORDER BY chain_index DESC LIMIT 1")
        prev_sig = rows[0]["signature"] if rows else GENESIS
        idx = (rows[0]["chain_index"] + 1) if rows else 1
        record = {
            "prediction_year": spec.prediction_year,
            "spec": spec.to_dict(),
            "result": result,
            "created_at": datetime.utcnow().isoformat(),
            "schema": "v0.2.0-hmac-chain",
            "note": "sealed prediction; append-only, HMAC chain signed",
        }
        h = self.simple_hash(record)
        record["hash"] = h
        record["chain_prev"] = prev_sig
        record["chain_index"] = idx
        sig = self._sign(record, prev_sig)
        record["hmac_sig"] = sig
        pred_id = f"pred_{spec.prediction_year}_{h[:12]}"
        path = self.dir / f"{pred_id}.json"
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("hmac_sig") == sig:
                return existing
            raise RuntimeError("existing prediction id but signature mismatch")
        text = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True)
        self._atomic_write(path, text)
        # 校验落盘
        disk = json.loads(path.read_text(encoding="utf-8"))
        if disk.get("hmac_sig") != sig or self.simple_hash(disk) != h:
            path.unlink()
            raise RuntimeError("prediction corrupted on write")
        try:
            self.meta.execute(
                "INSERT INTO predictions(pred_id,prediction_year,spec_json,result_json,result_hash,created_at,sealed) "
                "VALUES(?,?,?,?,?,?,1)",
                (pred_id, spec.prediction_year,
                 json.dumps(spec.to_dict(), ensure_ascii=False),
                 json.dumps(result, ensure_ascii=False), h,
                 datetime.utcnow().isoformat()),
            )
            self.meta.execute(
                "INSERT INTO prediction_chain VALUES(?,?,?,?,?)",
                (idx, pred_id, sig, prev_sig, datetime.utcnow().isoformat()),
            )
        except Exception:
            path.unlink()
            raise
        log.info("预测记录冻结(HMAC链): %s idx=%d sig=%s", pred_id, idx, sig[:12])
        return record

    def load(self, prediction_year: int) -> list[dict[str, Any]]:
        """读取指定年份的预测，逐条验证 HMAC 签名（篡改即抛错）。"""
        rows = self.meta.query(
            "SELECT * FROM predictions WHERE prediction_year=? ORDER BY pred_id",
            (prediction_year,),
        )
        out = []
        for r in rows:
            p = self.dir / f"{r['pred_id']}.json"
            if not p.exists():
                raise RuntimeError(f"prediction file missing: {r['pred_id']}")
            d = json.loads(p.read_text(encoding="utf-8"))
            # 链记录验证
            c = self.meta.query("SELECT * FROM prediction_chain WHERE pred_id=?",
                                (r["pred_id"],))
            if c:
                prev = c[0]["prev_signature"]
                if (d.get("hmac_sig") != c[0]["signature"]
                        or self._sign(d, prev) != c[0]["signature"]
                        or self.simple_hash(d) != d.get("hash")):
                    raise RuntimeError(f"prediction tampered: {r['pred_id']}")
            out.append(d)
        return out

    def verify_all(self) -> dict[str, Any]:
        chain = self.meta.query("SELECT * FROM prediction_chain ORDER BY chain_index")
        preds = self.meta.query("SELECT * FROM predictions ORDER BY prediction_year, pred_id")
        bad: list[str] = []
        prev = GENESIS
        # 链连续性与 HMAC
        for c in chain:
            p = self.dir / f"{c['pred_id']}.json"
            if not p.exists():
                bad.append(c["pred_id"]); prev = c["signature"]; continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if (d.get("chain_prev") != prev or d.get("chain_index") != c["chain_index"]
                    or d.get("hmac_sig") != c["signature"]
                    or self._sign(d, prev) != c["signature"]
                    or self.simple_hash(d) != d.get("hash")):
                bad.append(c["pred_id"])
            prev = c["signature"]
        # DB 中双份真相源一致性（仅对 v0.2 记录）
        for r in preds:
            p = self.dir / f"{r['pred_id']}.json"
            if not p.exists():
                bad.append(r["pred_id"]); continue
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("schema") == "v0.2.0-hmac-chain":
                if (json.dumps(d["spec"], ensure_ascii=False, sort_keys=True) !=
                        json.dumps(json.loads(r["spec_json"]), ensure_ascii=False, sort_keys=True)):
                    bad.append(r["pred_id"] + ":spec_json_mismatch")
                if (json.dumps(d["result"], ensure_ascii=False, sort_keys=True) !=
                        json.dumps(json.loads(r["result_json"]), ensure_ascii=False, sort_keys=True)):
                    bad.append(r["pred_id"] + ":result_json_mismatch")
        return {"records": len(preds), "chain_length": len(chain),
                "tampered": sorted(set(bad)), "ok": not bad,
                "mechanism": "HMAC-SHA256 + hash-chain + append-only-triggers"}
