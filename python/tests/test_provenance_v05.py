"""v0.5.0 P1：Embedding/模型/Agent 防泄漏与越权测试。"""
from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))


@pytest.fixture
def prov(tmp_path, monkeypatch):
    from quant import provenance_v05 as pv
    from quant.storage import MetadataDB
    monkeypatch.setattr(pv, "MetadataDB", lambda: MetadataDB(str(tmp_path / "prov.db")))
    return pv.ProvenanceV05()


def test_vector_time_isolation(prov):
    cutoff_2002 = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    prov.register_vector("v_ok", "doc_2001", "fake-embed", "v1",
                         "2001-10-01T00:00:00Z", "asof_2001")
    prov.register_vector("v_future", "doc_2003", "fake-embed", "v1",
                         "2003-05-01T00:00:00Z", "asof_2001")
    assert prov.vector_visible("v_ok", "asof_2001", cutoff_2002) is True
    assert prov.vector_visible("v_future", "asof_2001", cutoff_2002) is False


def test_model_provenance_and_time(prov, tmp_path):
    cutoff_2002 = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    w = tmp_path / "w.bin"
    w.write_bytes(b"model-weights")
    prov.register_model("m1", "asof_2001", "2001-12-31T23:59:59Z", "2001-12-31T23:59:59Z",
                        "feature_v1", {"mean": 0}, {"median": 0}, ["f1"],
                        {"lr": [0.01, 0.1]}, ["2001"], "20000101", {"python": "3.12"}, str(w))
    assert prov.model_visible("m1", cutoff_2002) is True
    row = prov.meta.query("SELECT * FROM model_provenance")[0]
    assert row["weight_hash"] and len(row["weight_hash"]) == 64


def test_model_future_training_blocked(prov, tmp_path):
    cutoff_2002 = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    w = tmp_path / "w2.bin"; w.write_bytes(b"x")
    prov.register_model("m_future", "asof_2003", "2003-06-01T00:00:00Z", "2003-06-01T00:00:00Z",
                        "f", {}, {}, [], {}, [], "1", {}, str(w))
    assert prov.model_visible("m_future", cutoff_2002) is False


def test_agent_access_log_and_violation(prov):
    prov.log_access("news_agent", "retrieve", "asof_2001", "2001-12-31T23:59:59Z",
                    ["doc_2001"], allowed=True)
    prov.log_access("news_agent", "retrieve_future", "asof_2001", "2001-12-31T23:59:59Z",
                    ["doc_2004"], allowed=False, detail="越权尝试")
    v = prov.agent_violations("news_agent")
    assert len(v) == 1 and v[0]["detail"] == "越权尝试"
