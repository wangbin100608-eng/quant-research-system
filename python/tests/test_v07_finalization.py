"""v0.7 收尾：高级统计 / Research Memory / 基线实验测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT))


def test_hac_newey_west():
    from src.statistics.advanced import newey_west_se
    rs = np.random.RandomState(1)
    x = rs.randn(200); y = 0.5 * x + rs.randn(200)
    r = newey_west_se(x, y)
    assert r["ok"] and r["se"] > 0 and r["n"] == 200
    assert newey_west_se(x[:3], y[:3])["ok"] is False


def test_block_bootstrap_and_multiple_testing():
    from src.statistics.advanced import (block_bootstrap_ci, benjamini_hochberg,
                                         holm_bonferroni)
    r = block_bootstrap_ci(np.random.RandomState(2).randn(300), block_size=10)
    assert r["ok"] and r["ci_low"] < r["mean"] < r["ci_high"]
    p = [0.001, 0.02, 0.3, 0.8]
    bh = benjamini_hochberg(p); ho = holm_bonferroni(p)
    assert bh["n_tests"] == 4 and bh["significant"][0]
    assert ho["p_adjusted"][0] >= bh["p_adjusted"][0]  # Holm 更保守


def test_cscv_dsr():
    from src.statistics.advanced import cscv, deflated_sharpe_ratio
    rs = np.random.RandomState(3)
    r = cscv([rs.randn(100) * 0.01, rs.randn(100) * 0.02])
    assert r["ok"] and 0 <= r["stability"] <= 1
    d = deflated_sharpe_ratio(1.2, 20)
    assert d["ok"] and 0 <= d["dsr"] <= 1


def test_research_memory_9_tables_and_lineage():
    from src.research_memory.store import ResearchMemoryV07
    rm = ResearchMemoryV07()
    tabs = rm.tables()
    assert len(tabs) >= 9
    rm.record_lineage("EXP-X", "hash1", "2000-01-01", "2001-12-31", "abc", "f1", "l1")
    assert len(rm.lineage_for("EXP-X")) >= 1


def test_model_baseline_report():
    import json
    p = ROOT / "data/reports/model_baselines_v07.json"
    if not p.exists():
        pytest.skip("baseline not generated")
    d = json.loads(p.read_text())
    assert len(d["groups"]) == 6
    assert any(v["status"] == "PASS" for v in d["groups"].values())
    assert any(v["status"] == "BLOCKED" for v in d["groups"].values())
