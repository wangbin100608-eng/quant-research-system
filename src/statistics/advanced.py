"""v0.7 P11 高级统计：HAC/Newey-West、Block Bootstrap、BH/Holm、PBO/CSCV/DSR。

所有函数为确定性、可复现（随机种子固定），不依赖真实试验结果即可单测。
"""
from __future__ import annotations

from typing import Any

import numpy as np
from scipy import stats as sps

SEED = 20000101


def newey_west_se(x: np.ndarray, y: np.ndarray, maxlags: int | None = None) -> dict[str, Any]:
    """OLS 斜率 HAC (Newey-West) 标准误。"""
    x = np.asarray(x, float); y = np.asarray(y, float)
    n = len(x)
    if n < 5:
        return {"ok": False, "reason": "样本不足"}
    if maxlags is None:
        maxlags = int(np.floor(4 * (n / 100) ** (2 / 9)))
    X = np.column_stack([np.ones(n), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    S = np.zeros((2, 2))
    for t in range(n):
        u = resid[t] * X[t]
        S += np.outer(u, u)
    for L in range(1, maxlags + 1):
        w = 1 - L / (maxlags + 1)
        G = np.zeros((2, 2))
        for t in range(L, n):
            ut = resid[t] * X[t]
            us = resid[t - L] * X[t - L]
            G += np.outer(ut, us)
        S += w * (G + G.T)
    xtx_inv = np.linalg.inv(X.T @ X)
    cov = xtx_inv @ S @ xtx_inv
    se = float(np.sqrt(max(cov[1, 1], 0)))
    t_stat = float(beta[1] / se) if se > 0 else 0.0
    return {"ok": True, "slope": float(beta[1]), "se": se, "t_stat": t_stat,
            "p_value": float(2 * (1 - sps.norm.cdf(abs(t_stat)))), "n": n,
            "maxlags": maxlags, "method": "newey_west"}


def block_bootstrap_ci(values: np.ndarray, block_size: int = 5,
                       n_boot: int = 2000, alpha: float = 0.05,
                       seed: int = SEED) -> dict[str, Any]:
    """移动块 Bootstrap（保留时间序列相关性）。"""
    v = np.asarray(values, float); v = v[~np.isnan(v)]
    n = len(v)
    if n < block_size + 2:
        return {"ok": False, "reason": "样本不足"}
    rs = np.random.RandomState(seed)
    means = []
    n_blocks = int(np.ceil(n / block_size))
    for _ in range(n_boot):
        starts = rs.randint(0, n - block_size + 1, size=n_blocks)
        sample = np.concatenate([v[s:s + block_size] for s in starts])[:n]
        means.append(np.mean(sample))
    means = np.array(means)
    return {"ok": True, "n": n, "block_size": block_size, "mean": float(np.mean(v)),
            "ci_low": float(np.percentile(means, 100 * alpha / 2)),
            "ci_high": float(np.percentile(means, 100 * (1 - alpha / 2))),
            "n_boot": n_boot, "method": "moving_block_bootstrap"}


def benjamini_hochberg(pvalues: list[float], alpha: float = 0.05) -> dict[str, Any]:
    p = np.asarray(pvalues, float)
    m = len(p)
    if m == 0:
        return {"ok": False, "reason": "无 p 值"}
    order = np.argsort(p)
    ranked = p[order]
    adj = np.empty(m)
    prev = 1.0
    for i in range(m - 1, -1, -1):
        rank = i + 1
        val = min(prev, ranked[i] * m / rank)
        adj[i] = val
        prev = val
    out = np.empty(m)
    out[order] = adj
    return {"ok": True, "alpha": alpha, "p_adjusted": out.tolist(),
            "significant": [bool(x <= alpha) for x in out], "n_tests": m,
            "method": "benjamini_hochberg"}


def holm_bonferroni(pvalues: list[float], alpha: float = 0.05) -> dict[str, Any]:
    p = np.asarray(pvalues, float)
    m = len(p)
    if m == 0:
        return {"ok": False, "reason": "无 p 值"}
    order = np.argsort(p)
    adj = np.empty(m)
    running = 0.0
    for rank, idx in enumerate(order, start=1):
        val = min(1.0, max(running, (m - rank + 1) * p[idx]))
        adj[idx] = val
        running = val
    return {"ok": True, "alpha": alpha, "p_adjusted": adj.tolist(),
            "significant": [bool(x <= alpha) for x in adj], "n_tests": m,
            "method": "holm"}


def cscv(returns_by_strategy: list[np.ndarray]) -> dict[str, Any]:
    """Cross-Validation over Combinations of Signals（简化 CSCV）。"""
    if len(returns_by_strategy) < 2:
        return {"ok": False, "reason": "至少两个策略"}
    n = min(len(r) for r in returns_by_strategy)
    if n < 10:
        return {"ok": False, "reason": "样本不足"}
    R = np.column_stack([r[:n] for r in returns_by_strategy])
    means = R.mean(axis=0)
    best = int(np.argmax(means))
    # 留一法检验最优选择稳定性
    stable = 0
    for i in range(n):
        m = np.delete(R, i, axis=0).mean(axis=0)
        stable += int(np.argmax(m) == best)
    return {"ok": True, "best_strategy": best, "stability": stable / n,
            "n_observations": n, "n_strategies": len(returns_by_strategy),
            "method": "cscv_loo"}


def deflated_sharpe_ratio(sharpe: float, n_trials: int,
                          skew: float = 0.0, kurtosis: float = 3.0) -> dict[str, Any]:
    """Deflated Sharpe Ratio（Bailey & Lopez de Prado 简化版，仅作诊断）。"""
    if n_trials < 1:
        return {"ok": False, "reason": "试验次数无效"}
    z = sps.norm.ppf(1 - 1 / n_trials)
    expected_max = float(z)
    dsr = float(sps.norm.cdf((sharpe - expected_max) /
                             max(np.sqrt(max(kurtosis - 1, 1e-6)), 1e-6)))
    return {"ok": True, "dsr": dsr, "n_trials": n_trials, "observed_sharpe": sharpe,
            "method": "deflated_sharpe_diagnostic",
            "note": "诊断指标，不替代时间序列验证"}
