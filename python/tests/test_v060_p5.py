"""v0.6.0 P5：统计可信度测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.stats_v060 import bootstrap_ci, bonferroni, evidence_level, mann_whitney


def test_bootstrap_ci():
    r = bootstrap_ci(np.random.RandomState(1).randn(200))
    assert r["ok"] and r["ci_low"] < r["mean"] < r["ci_high"]
    assert bootstrap_ci(np.array([1.0]))["ok"] is False


def test_bonferroni():
    r = bonferroni([0.001, 0.2, 0.4])
    assert r["n_tests"] == 3 and r["corrected_alpha"] == pytest.approx(0.05 / 3)
    assert r["significant"][0] is True and r["significant"][2] is False


def test_evidence_levels():
    assert evidence_level(10) == "insufficient_evidence"
    assert evidence_level(100) == "preliminary_evidence"
    assert evidence_level(50, multiple_testing=True) == "preliminary_evidence_unadjusted"


def test_mann_whitney():
    a = np.random.RandomState(2).randn(100)
    b = np.random.RandomState(3).randn(100) + 1
    r = mann_whitney(a, b)
    assert r["ok"] and r["pvalue"] < 0.05
