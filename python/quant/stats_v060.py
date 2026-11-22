"""v0.6.0 P5：研究结果可信度（Bootstrap CI / 多重检验校正 / 证据等级）。"""
from __future__ import annotations

from typing import Any

import numpy as np

from scipy import stats as sps


def bootstrap_ci(values: np.ndarray, n_boot: int = 2000,
                 seed: int = 20000101, alpha: float = 0.05) -> dict[str, Any]:
    """Bootstrap 均值置信区间（研究指标不确定度）。"""
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 2:
        return {"ok": False, "reason": "样本不足", "n": len(v)}
    rs = np.random.RandomState(seed)
    means = np.array([np.mean(rs.choice(v, size=len(v), replace=True))
                      for _ in range(n_boot)])
    return {"ok": True, "n": len(v), "mean": float(np.mean(v)),
            "ci_low": float(np.percentile(means, 100 * alpha / 2)),
            "ci_high": float(np.percentile(means, 100 * (1 - alpha / 2))),
            "alpha": alpha, "n_boot": n_boot}


def bonferroni(pvalues: list[float], alpha: float = 0.05) -> dict[str, Any]:
    """Bonferroni 多重检验校正。"""
    p = np.asarray(pvalues, dtype=float)
    if len(p) == 0:
        return {"ok": False, "reason": "无 p 值"}
    m = len(p)
    return {"ok": True, "alpha": alpha, "corrected_alpha": alpha / m,
            "significant": [bool(x < alpha / m) for x in p], "n_tests": m}


def evidence_level(n: int, ci: dict | None = None,
                   multiple_testing: bool = False) -> str:
    """统计证据分级：样本不足/证据不足/初步证据/稳健证据。"""
    if n < 30:
        return "insufficient_evidence"
    if ci and not ci.get("ok"):
        return "insufficient_evidence"
    if multiple_testing:
        return "preliminary_evidence_unadjusted"
    if n >= 200 and ci and (ci["ci_high"] - ci["ci_low"]) < 0.1:
        return "robust_evidence"
    return "preliminary_evidence"


def mann_whitney(a: np.ndarray, b: np.ndarray) -> dict[str, Any]:
    """非参数两组比较（不假设正态）。"""
    a = np.asarray(a, float); a = a[~np.isnan(a)]
    b = np.asarray(b, float); b = b[~np.isnan(b)]
    if len(a) < 3 or len(b) < 3:
        return {"ok": False, "reason": "样本不足"}
    u, p = sps.mannwhitneyu(a, b, alternative="two-sided")
    return {"ok": True, "statistic": float(u), "pvalue": float(p),
            "n_a": len(a), "n_b": len(b)}
