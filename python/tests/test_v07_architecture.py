"""v0.7.0 P4-P19：新架构模块测试。"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT))


def test_p4_timeguard_v07():
    from src.data.point_in_time.timeguard_v07 import TimeGuardV07
    from quant.timeguard_reader import TimeGuardReadError
    tg = TimeGuardV07()
    cut = datetime(2001, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    assert tg.validate_record({"symbol": "X", "available_at": "2001-06-01T00:00:00Z"}, cut)
    with pytest.raises(Exception):
        tg.validate_record({"symbol": "X", "available_at": "2002-01-01T00:00:00Z"}, cut)
    assert tg.validate_model_provenance(
        {"train_max_available_at": "2001-06-01T00:00:00Z"}, cut)
    with pytest.raises(Exception):
        tg.validate_model_provenance(
            {"train_max_available_at": "2003-01-01T00:00:00Z"}, cut)


def test_p5_feature_registry(tmp_path):
    from src.features.registry import FeatureRegistry
    r = FeatureRegistry(str(tmp_path / "reg.json"))
    assert len(r.specs) >= 6
    spec = r.get("FEAT_MOMENTUM_6M")
    assert spec.category == "price" and spec.version
    p = r.save()
    assert p.exists()


def test_p7_label_factory(tmp_path):
    import pyarrow as pa, pyarrow.parquet as pq
    from src.labels.factory import build_labels
    p = tmp_path / "raw.parquet"
    n = 400
    days = ["2001-01-01T00:00:00Z"] * (n // 2) + ["2003-01-01T00:00:00Z"] * (n // 2)
    pq.write_table(pa.table({"symbol": [f"S{i}" for i in range(n)],
                             "market": ["US"] * n,
                             "domain": ["price"] * n,
                             "available_time": days,
                             "payload": [__import__("json").dumps({"close": 10 + i % 5})
                                         for i in range(n)],
                             "source": ["real:test"] * n}), str(p))
    labels = build_labels(p, str(tmp_path / "labels"))
    for h in ("1d", "5d", "20d"):
        assert "label_id" in labels[h].column_names
        assert "label_version" in labels[h].column_names


def test_p8_p9_hypothesis_dedup(tmp_path):
    from src.hypothesis.engine import HypothesisEngine
    eng = HypothesisEngine(str(tmp_path / "hyp.json"))
    h = {"hypothesis_id": "HYP-1", "market": "US", "horizon": "5d",
         "target": "future_return", "feature_ids": ["FEATURE_A"],
         "expected_relation": "positive",
         "sample_period": {"start": "2015-01-01", "end": "2020-12-31"},
         "validation_plan": "walk_forward", "falsification_criteria": [],
         "rationale": "test"}
    st, rec = eng.submit(h)
    assert st == "PROPOSED"
    st2, _ = eng.submit(h)
    assert st2 == "DUPLICATE"
    bad = dict(h); bad["market"] = "MARS"
    st3, _ = eng.submit(bad)
    assert st3 == "REJECTED_SCHEMA"


def test_p10_p13_research_lab():
    from src.experiments.lab import ResearchLab
    lab = ResearchLab()
    pred = lambda X: np.zeros(len(X))
    r = lab.run_experiment("HYP-TEST", {"feature_version": "f1"},
                           pred, np.zeros((40, 2)), np.random.RandomState(1).randn(40))
    assert "experiment_id" in r and r["status"] in ("NO_EVIDENCE", "PROMISING", "FAILED")
    fail = lab.run_experiment("HYP-FAIL", {},
                              lambda X: (_ for _ in ()).throw(RuntimeError("boom")),
                              np.zeros((5, 2)), np.zeros(5))
    assert fail["status"] == "FAILED" and "boom" in fail["failure_reason"]


def test_p14_p15_scheduler():
    from src.scheduler.manager import ResourceManager, ResourcePolicy, ResearchScheduler
    mgr = ResourceManager(ResourcePolicy(max_formal_experiments_per_day=1))
    sch = ResearchScheduler(mgr)
    assert sch.submit({"hypothesis_id": "H1"}) == "QUEUED"
    r = sch.run_next(lambda h: "ok")
    assert r["status"] == "RUN" and r["ok"]
    assert sch.run_next(lambda h: "ok") == {"status": "EMPTY"}


def test_p16_p17_shadow_mode():
    from src.forecasting.shadow import ShadowMode, build_model_groups
    assert len(build_model_groups()) == 6
    import tempfile
    sm = ShadowMode(str(Path(tempfile.mkdtemp()) / "shadow.json"))
    rec = sm.freeze_prediction("US", "5d", "2024-01-01", "AAPL", 0.02,
                               confidence_interval=(-0.01, 0.05))
    assert rec["shadow_only"] and not rec["real_orders"] and rec["record_sha256"]
    ev = sm.evaluate_mature(rec["prediction_id"], 0.03)
    assert abs(ev["error"] - 0.01) < 1e-9


def test_p18_dashboard(tmp_path):
    import src.reporting.dashboard as d
    p = d.build_research_dashboard(str(tmp_path / "dash.json"))
    assert p["version"] == "0.7.0"
    assert len(p["status_enum"]) == 9
    assert len(p["pages"]) == 5
