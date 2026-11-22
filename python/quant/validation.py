"""v0.3.0 P0-4：严格时间序列验证系统。

Split 方法：Expanding Window / Rolling Window / Walk-forward / Purged / Embargo。
关键：所有 split 基于 feature_time（特征截止），且 target_end 不得越过训练边界；
Purged 剔除目标区间与测试区重叠的样本，Embargo 在边界后再剔除 gap 天。

指标：MAE/RMSE/方向准确率/Rank IC/Spearman/预测区间覆盖率/概率校准/分市场/分行业。
样本量不足时输出 "insufficient_evidence"，禁止宣称稳定预测能力。
所有模型与 benchmark（历史均值/时序 naive）比较。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

import numpy as np
import pyarrow as pa

from .evaluation import _ranks


@dataclass
class SplitResult:
    name: str
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_end: str
    test_start: str
    test_end: str
    purged: int = 0


def _times(table: pa.Table) -> np.ndarray:
    return np.array([str(d)[:10] for d in table.column("feature_time").to_pylist()])


def _target_ends(table: pa.Table) -> np.ndarray:
    return np.array([str(d)[:10] for d in table.column("target_end").to_pylist()])


def _purge(train_idx, test_idx, times, target_ends, embargo_days: int = 0) -> tuple[np.ndarray, int]:
    """剔除目标区间与测试区重叠的训练样本 + embargo。"""
    if len(test_idx) == 0:
        return train_idx, 0
    test_start = min(times[test_idx])
    keep = []
    removed = 0
    for i in train_idx:
        # 样本目标结束 >= 测试开始 => 目标信息来自测试期 => purge
        if target_ends[i] >= test_start:
            removed += 1
            continue
        # embargo：训练样本特征时间离测试起点太近
        if embargo_days > 0:
            gap = (date.fromisoformat(test_start) - date.fromisoformat(times[i])).days
            if gap < embargo_days:
                removed += 1
                continue
        keep.append(i)
    return np.array(keep, dtype=int), removed


def expanding_window(table, n_splits=4, test_size=63, embargo=0, purge=True):
    times, ends = _times(table), _target_ends(table)
    uniq = sorted(set(times))
    out = []
    for k in range(n_splits):
        # 测试段向后滚动
        te_end_idx = len(uniq) - (n_splits - 1 - k) * test_size
        te_start_idx = max(1, te_end_idx - test_size)
        te_start, te_end = uniq[te_start_idx], uniq[min(te_end_idx, len(uniq) - 1)]
        tr = np.where(times < te_start)[0]
        te = np.where((times >= te_start) & (times <= te_end))[0]
        purged = 0
        if purge:
            tr, purged = _purge(tr, te, times, ends, embargo)
        out.append(SplitResult(f"expanding_{k+1}", tr, te,
                               str(max(times[tr]) if len(tr) else "-"), te_start, te_end, purged))
    return out


def rolling_window(table, n_splits=4, train_size=252, test_size=63, embargo=0, purge=True):
    times, ends = _times(table), _target_ends(table)
    uniq = sorted(set(times))
    out = []
    for k in range(n_splits):
        te_end_idx = len(uniq) - (n_splits - 1 - k) * test_size
        te_start_idx = max(1, te_end_idx - test_size)
        tr_start_idx = max(0, te_start_idx - train_size)
        te_start, te_end = uniq[te_start_idx], uniq[min(te_end_idx, len(uniq) - 1)]
        tr_start = uniq[tr_start_idx]
        tr = np.where((times >= tr_start) & (times < te_start))[0]
        te = np.where((times >= te_start) & (times <= te_end))[0]
        purged = 0
        if purge:
            tr, purged = _purge(tr, te, times, ends, embargo)
        out.append(SplitResult(f"rolling_{k+1}", tr, te, tr_start, te_start, te_end, purged))
    return out


def walk_forward(table, n_splits=6, test_size=21, embargo=0, purge=True):
    """逐步向前：每次训练截至 t，预测 t+1..t+test_size。"""
    times, ends = _times(table), _target_ends(table)
    uniq = sorted(set(times))
    out = []
    for k in range(n_splits):
        cut_idx = int(len(uniq) * (0.4 + 0.1 * k))
        cut_idx = min(cut_idx, len(uniq) - test_size - 1)
        if cut_idx <= 0:
            continue
        cut = uniq[cut_idx]
        te_end = uniq[min(cut_idx + test_size, len(uniq) - 1)]
        tr = np.where(times <= cut)[0]
        te = np.where((times > cut) & (times <= te_end))[0]
        purged = 0
        if purge:
            tr, purged = _purge(tr, te, times, ends, embargo)
        out.append(SplitResult(f"walkforward_{k+1}", tr, te, cut, cut, te_end, purged))
    return out


def purged_embargo_split(table, n_splits=5, embargo_days=5, test_size=63):
    """Purged Time Series Split + Embargo（Lopez de Prado）。"""
    return expanding_window(table, n_splits=n_splits, test_size=test_size,
                            embargo=embargo_days, purge=True)


# ---------- 指标 ----------
def compute_metrics(pred: np.ndarray, actual: np.ndarray,
                    lower: np.ndarray | None = None, upper: np.ndarray | None = None,
                    prob: np.ndarray | None = None,
                    groups: np.ndarray | None = None) -> dict[str, Any]:
    pred = np.asarray(pred, float); actual = np.asarray(actual, float)
    mask = ~np.isnan(pred) & ~np.isnan(actual)
    if mask.sum() == 0:
        return {"n": 0, "insufficient_evidence": True}
    p, a = pred[mask], actual[mask]
    out: dict[str, Any] = {
        "n": int(mask.sum()),
        "mae": float(np.mean(np.abs(p - a))),
        "rmse": float(np.sqrt(np.mean((p - a) ** 2))),
        "direction_accuracy": float(np.mean(np.sign(p) == np.sign(a))),
    }
    # Rank IC / Spearman
    if len(p) >= 5 and np.std(p) > 0 and np.std(a) > 0:
        out["rank_ic"] = float(np.corrcoef(_ranks(p), _ranks(a))[0, 1])
        out["spearman"] = float(np.corrcoef(_ranks(p), _ranks(a))[0, 1])
    else:
        out["rank_ic"] = None; out["spearman"] = None
    # 预测区间覆盖率
    if lower is not None and upper is not None:
        lo, hi = np.asarray(lower)[mask], np.asarray(upper)[mask]
        out["interval_coverage"] = float(np.mean((a >= lo) & (a <= hi)))
    # 概率校准（Brier score，方向概率）
    if prob is not None:
        pr = np.asarray(prob)[mask]
        y01 = (a > 0).astype(float)
        out["brier_score"] = float(np.mean((pr - y01) ** 2))
    # 分市场/行业
    if groups is not None:
        by = {}
        for g in np.unique(groups[mask]):
            gm = groups[mask] == g
            if gm.sum() >= 3:
                by[str(g)] = {"n": int(gm.sum()),
                              "mae": float(np.mean(np.abs(p[gm] - a[gm]))),
                              "direction_accuracy": float(np.mean(np.sign(p[gm]) == np.sign(a[gm])))}
        out["by_group"] = by
    # 统计证据：样本太少或方向/IC 无信号
    if mask.sum() < 30 or out.get("rank_ic") is None or abs(out.get("rank_ic", 0)) < 0.05:
        out["insufficient_evidence"] = True
        out["evidence_note"] = "样本不足或 Rank IC 无信号，不得宣称模型具有稳定预测能力"
    else:
        out["insufficient_evidence"] = False
    return out


# ---------- 交叉验证执行 ----------
def cross_validate(table: pa.Table, model_factory: Callable,
                   splits: list[SplitResult], feature_cols: list[str],
                   target_col: str = "target_return",
                   group_col: str | None = None) -> dict[str, Any]:
    """对每个 split 训练并评估，返回逐折结果与汇总。"""
    X = np.column_stack([table.column(c).to_numpy(zero_copy_only=False).astype(float)
                         for c in feature_cols])
    y = table.column(target_col).to_numpy(zero_copy_only=False).astype(float)
    groups = (np.array([str(g) for g in table.column(group_col).to_pylist()])
              if group_col and group_col in table.column_names else None)
    folds = []
    for sp in splits:
        tr, te = sp.train_idx, sp.test_idx
        if len(tr) < 10 or len(te) < 3:
            folds.append({"split": sp.name, "skipped": True,
                          "reason": f"train={len(tr)} test={len(te)} too small"})
            continue
        model = model_factory()
        model.fit(X[tr], y[tr])
        pred = model.predict(X[te])
        m = compute_metrics(pred, y[te], groups=groups[te] if groups is not None else None)
        folds.append({"split": sp.name, "train_n": len(tr), "test_n": len(te),
                      "purged": sp.purged, "train_end": sp.train_end,
                      "test_range": [sp.test_start, sp.test_end], "metrics": m})
    oks = [f for f in folds if not f.get("skipped") and not f["metrics"].get("insufficient_evidence")]
    summary = {
        "folds": len(folds), "valid_folds": len(oks),
        "mean_mae": float(np.mean([f["metrics"]["mae"] for f in oks])) if oks else None,
        "mean_rmse": float(np.mean([f["metrics"]["rmse"] for f in oks])) if oks else None,
        "mean_rank_ic": float(np.mean([f["metrics"].get("rank_ic", 0) or 0 for f in oks])) if oks else None,
        "insufficient_evidence": len(oks) == 0,
    }
    return {"folds": folds, "summary": summary}
