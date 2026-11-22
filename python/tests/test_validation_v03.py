"""v0.3.0 P0-3/P0-4：多期限样本与严格验证系统测试。"""
from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from quant.horizons import HORIZONS, build_horizon_samples
from quant.validation import (expanding_window, rolling_window, walk_forward,
                              purged_embargo_split, compute_metrics, cross_validate)


def _sample_table(n=400):
    dates = [ f"{(2000 + i // 250)}-01-01" for i in range(n) ]  # 简化日期序列
    import datetime
    base = datetime.date(2000, 1, 1)
    ft = [(base + datetime.timedelta(days=i*3)).isoformat() for i in range(n)]
    te = [(base + datetime.timedelta(days=i*3+30)).isoformat() for i in range(n)]
    return pa.table({
        "symbol": ["X"]*n, "feature_time": ft, "target_end": te,
        "f1": np.random.RandomState(0).randn(n),
        "f2": np.random.RandomState(1).randn(n),
        "target_return": np.random.RandomState(2).randn(n)*0.01,
        "market": ["US"]*n,
    })


def test_horizons_defined():
    assert set(HORIZONS) == {"1d", "5d", "20d", "1m", "3m", "6m", "1y"}
    assert HORIZONS["1d"] == 1 and HORIZONS["1y"] == 252


def test_all_split_methods_produce_splits():
    t = _sample_table()
    for fn in (expanding_window, rolling_window, walk_forward, purged_embargo_split):
        splits = fn(t)
        assert len(splits) >= 3, fn.__name__
        for s in splits:
            assert len(s.train_idx) > 0 and len(s.test_idx) > 0


def test_purge_removes_overlapping_targets():
    """核心：训练样本若 target_end 越过测试起点，必须被 purge。"""
    t = _sample_table()
    splits = expanding_window(t, n_splits=2, test_size=40, purge=True, embargo=0)
    assert any(s.purged > 0 for s in splits), "purged 未剔除任何重叠样本"
    for s in splits:
        ends = t.column("target_end").to_pylist()
        for i in s.train_idx:
            assert ends[i] < s.test_start, "purge 后有训练样本目标区间越过测试起点"


def test_embargo_adds_gap():
    t = _sample_table()
    no_emb = expanding_window(t, n_splits=3, test_size=40, embargo=0)
    with_emb = expanding_window(t, n_splits=3, test_size=40, embargo=60)
    assert sum(s.purged for s in with_emb) > sum(s.purged for s in no_emb)


def test_metrics_insufficient_evidence():
    small = compute_metrics(np.array([0.1, 0.2]), np.array([0.1, 0.3]))
    assert small["insufficient_evidence"] is True
    rs = np.random.RandomState(3)
    y = rs.randn(200)*0.01
    pred = y + rs.randn(200)*0.005
    m = compute_metrics(pred, y)
    assert set(["mae", "rmse", "direction_accuracy", "rank_ic", "spearman"]).issubset(m)
    assert m["insufficient_evidence"] is False


def test_metrics_interval_and_calibration():
    rs = np.random.RandomState(4)
    y = rs.randn(100)
    pred = y*0.9
    m = compute_metrics(pred, y, lower=pred-0.1, upper=pred+0.1,
                        prob=1/(1+np.exp(-pred)))
    assert 0 <= m["interval_coverage"] <= 1
    assert m["brier_score"] >= 0


def test_cross_validate_runs_and_compares_benchmark():
    from sklearn.linear_model import LinearRegression
    from sklearn.dummy import DummyRegressor
    t = _sample_table()
    splits = walk_forward(t, n_splits=3, test_size=20, purge=True)
    res_model = cross_validate(t, LinearRegression, splits, ["f1", "f2"])
    res_bench = cross_validate(t, lambda: DummyRegressor(strategy="mean"),
                               splits, ["f1", "f2"])
    assert res_model["summary"]["folds"] >= 2
    assert "mean_mae" in res_bench["summary"]
