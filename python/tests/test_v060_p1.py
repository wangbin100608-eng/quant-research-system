"""v0.6.0 P1：强制门禁测试（Agent/Embedding/模型）+ 预测冻结必需字段。"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python/scripts"))


@pytest.fixture
def enf(tmp_path, monkeypatch):
    from quant.enforcement_v060 import TimeEnforcementV060
    from quant.storage import MetadataDB
    import quant.enforcement_v060 as m
    monkeypatch.setattr(m, "MetadataDB", lambda: MetadataDB(str(tmp_path / "enf.db")))
    return m.TimeEnforcementV060()


def test_agent_forced_gate_and_violation_persisted(enf):
    cut = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    ok, reason = enf.agent_call("news", "retrieve", "asof_2001", cut,
                                {"old": "2001-06-01T00:00:00Z"})
    assert ok and reason == ""
    ok, reason = enf.agent_call("news", "retrieve", "asof_2001", cut,
                                {"future": "2003-01-01T00:00:00Z"})
    assert not ok and "future_document" in reason
    ok, reason = enf.agent_call("news", "retrieve", None, cut)
    assert not ok and "missing_snapshot" in reason
    violations = enf.agent_violations("news")
    assert len(violations) == 2


def test_embedding_full_provenance_and_snapshot_gate(enf, tmp_path):
    doc = tmp_path / "doc.txt"; doc.write_text("原文")
    model = tmp_path / "model.bin"; model.write_bytes(b"weights")
    cut = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    enf.register_vector("v1", "d1", str(doc), "fake-embed", str(model),
                        {"dim": 8}, "kb_v1", "asof_2001", cut, "vec_hash_1",
                        generated_at=datetime(2001, 12, 1, tzinfo=timezone.utc))
    row = enf.meta.query("SELECT * FROM enforce_vectors")[0]
    assert len(row["doc_sha256"]) == 64 and len(row["model_file_hash"]) == 64
    assert row["cutoff"] == cut.isoformat()
    assert enf.vector_visible("v1", "asof_2001", cut) is True
    assert enf.vector_visible("v1", "asof_2002", cut) is False  # 跨快照不可见
    earlier = datetime(2001, 1, 1, tzinfo=timezone.utc)
    assert enf.vector_visible("v1", "asof_2001", earlier) is False  # 未来生成不可见


def test_model_selection_no_future_test(enf):
    cut = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    ok, reason = enf.register_model("m1", "asof_2001", "2001-12-31T23:59:59Z", cut,
                                    "train+validation only", ["2004-01-01", "2004-12-31"],
                                    {"lr": [0.01]})
    assert ok
    bad, reason = enf.register_model("m2", "asof_2001", "2001-12-31T23:59:59Z", cut,
                                     "chosen by 2004-12-31 test accuracy",
                                     ["2004-01-01", "2004-12-31"], {})
    assert not bad and "selection_uses_final_test" in reason


def test_freeze_required_fields_and_hashes(tmp_path, monkeypatch):
    import prediction_freeze_v05 as pf
    from quant.storage import MetadataDB
    monkeypatch.setattr(pf, "MetadataDB", lambda: MetadataDB(str(tmp_path / "f.db")))
    f = pf.FreezeV05()
    with pytest.raises(ValueError):
        f.save_freeze("p", 2002, "", "bm", "f", "t", "c", {})
    r = f.save_freeze("p", 2002, "asof_2001", "bm", "f", "2001-12-31T23:59:59Z",
                      "2001-12-31T23:59:59Z", {"pred": 1},
                      prediction_input={"f1": 0.1}, key_version="v2")
    assert len(r["input_hash"]) == 64 and r["key_version"] == "v2"
    assert "seal_key" not in json.dumps(r, ensure_ascii=False)  # 不含密钥本体
    assert f.verify_freeze()["ok"]
