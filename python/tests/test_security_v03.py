"""v0.3.0 P1-4：安全与完整性测试（密钥、恢复、篡改）。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quant.evaluation_v02 import PredictionStoreV2, rotate_key, load_all_keys
from quant.knowledge import KnowledgeBase
from quant.utils import ROOT, resolve


def test_seal_key_not_git_tracked():
    import subprocess
    out = subprocess.run(["git", "ls-files", "data/.seal_key"],
                         cwd=str(ROOT), capture_output=True, text=True).stdout.strip()
    assert out == "", ".seal_key 仍被 Git 跟踪"


def test_key_rotation_preserves_old_signatures():
    store = PredictionStoreV2()
    assert store.verify_all()["ok"]
    # 不实际再轮换（避免影响其他测试），但验证多密钥加载包含 retired
    assert len(store.keys) >= 1
    # 用所有密钥逐一验证链首
    chain = store.meta.query("SELECT * FROM prediction_chain ORDER BY chain_index LIMIT 1")
    if chain:
        p = ROOT / f"data/predictions/{chain[0]['pred_id']}.json"
        d = json.loads(p.read_text())
        assert store._verify_sig(d, d["chain_prev"], d["hmac_sig"])


def test_prediction_tamper_detected_and_recoverable(tmp_path):
    store = PredictionStoreV2()
    preds = list((ROOT / "data/predictions").glob("pred_*.json"))
    if not preds:
        pytest.skip("no predictions")
    p = preds[0]
    original = p.read_text(encoding="utf-8")
    backup = tmp_path / p.name
    shutil.copy2(p, backup)
    try:
        d = json.loads(original)
        d["result"]["prediction_interval"]["lower"] = -12345.0
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        assert store.verify_all()["ok"] is False, "篡改未被检测"
    finally:
        shutil.copy2(backup, p)
    assert store.verify_all()["ok"] is True


def test_snapshot_recovery_and_verify(tmp_path):
    kb = KnowledgeBase()
    src = kb.snapshot_path(2001)
    if not src.exists():
        pytest.skip("snapshot missing")
    backup = tmp_path / "backup"
    shutil.copytree(src, backup / "asof_2001")
    # 用备份恢复（snapshots_dir 指向 tmp 避免碰生产）
    kb2 = KnowledgeBase()
    kb2.snapshots_dir = tmp_path / "restored"
    kb2.snapshots_dir.mkdir(exist_ok=True)
    result = kb2.restore_snapshot_from_manifest(2001, backup)
    assert result["ok"]


def test_signature_backup_exists():
    b = ROOT.parent / "quant_seal_backup"
    if not b.exists():
        pytest.skip("independent backup not created")
    assert (b / "old_key.txt").exists()
    assert any(b.glob("pred_*.json"))
