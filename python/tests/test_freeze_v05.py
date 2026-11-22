"""v0.5.0 P0：预测冻结完整溯源测试（临时库，不动真实 DB）。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python/scripts"))


@pytest.fixture
def freeze(tmp_path, monkeypatch):
    import prediction_freeze_v05 as pf
    from quant.storage import MetadataDB
    monkeypatch.setattr(pf, "MetadataDB", lambda: MetadataDB(str(tmp_path / "freeze.db")))
    return pf.FreezeV05()


def test_freeze_records_full_provenance(freeze):
    r = freeze.save_freeze("pred_x", 2002, "asof_2001", "bm_v1", "feature_v1",
                           "2001-12-31T23:59:59Z", "2001-12-31T23:59:59Z", {"p": 1.0})
    for k in ("prediction_id", "prediction_year", "data_snapshot_id", "model_version",
              "feature_version", "code_commit", "training_max_available_at",
              "prediction_cutoff", "frozen_at", "prediction_hash", "hmac_sig"):
        assert k in r
    assert r["status"] == "frozen" if "status" in r else True


def test_freeze_chain_and_verify(freeze):
    freeze.save_freeze("p1", 2002, "asof_2001", "bm", "f", "2001-12-31T23:59:59Z",
                       "2001-12-31T23:59:59Z", {"p": 1})
    freeze.save_freeze("p2", 2003, "asof_2002", "bm", "f", "2002-12-31T23:59:59Z",
                       "2002-12-31T23:59:59Z", {"p": 2})
    v = freeze.verify_freeze()
    assert v["ok"] and v["records"] == 2


def test_amend_requires_reason(freeze):
    with pytest.raises(ValueError):
        freeze.amend("p", 2002, "asof_2001", "bm", "f", "t", "c", {}, reason="")
    r = freeze.amend("p", 2002, "asof_2001", "bm", "f", "t", "c", {},
                     reason="修正：修正了特征计算错误")
    assert r["is_amendment"] == 1
    assert "修正" in r["amend_reason"]


def test_frozen_prediction_cannot_be_modified(freeze):
    freeze.save_freeze("p1", 2002, "asof_2001", "bm", "f", "t", "c", {"p": 1})
    with pytest.raises(Exception):
        freeze.meta.execute("UPDATE prediction_freeze_v05 SET status='x'")
    with pytest.raises(Exception):
        freeze.meta.execute("DELETE FROM prediction_freeze_v05")
