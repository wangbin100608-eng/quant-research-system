"""模型系统 v0.2.0：多模型统一接口与基准对比。

模型：历史均值 / 时序基准 / LightGBM / XGBoost / CatBoost / 可选 PyTorch MLP。
所有模型使用相同特征版本与时间边界（trained_to <= cutoff），记录版本/训练范围/快照。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa

from .contracts import PredictionSpec
from .timeguard import LeakError, TimeTravelEngine
from .utils import get_config, resolve
from .logging_util import log

FEATURE_NUMERIC = [
    "trailing_mean_return_1y", "trailing_mean_return_2y",
    "trailing_vol_1y", "momentum_6m", "avg_turnover_log", "eps_latest",
]

BENCHMARK_VERSION = "benchmark_mean_v1"
LGBM_VERSION = "lightgbm_v1"
XGB_VERSION = "xgboost_v1"
CAT_VERSION = "catboost_v1"
TS_VERSION = "timeseries_naive_v1"
MLP_VERSION = "torch_mlp_v1"

SEED = 20000101


class ModelRegistry:
    def __init__(self, snapshot_path: Path, spec: PredictionSpec):
        self.dir = Path(snapshot_path) / "models"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.spec = spec

    def save(self, name: str, model: Any, stats: dict[str, Any]) -> dict[str, Any]:
        import pickle
        meta = {
            "model_name": name,
            "version": stats.get("version", name),
            "built_year": self.spec.prediction_year,
            "trained_to": self.spec.cutoff_date.isoformat(),
            "training_start": self.spec.training_start_date.isoformat(),
            "feature_version": stats.get("feature_version"),
            "knowledge_snapshot_id": self.spec.knowledge_snapshot_id,
            "stats": stats,
            "created_at": datetime.utcnow().isoformat(),
        }
        with open(self.dir / f"{name}.pkl", "wb") as f:
            pickle.dump(model, f)
        (self.dir / f"{name}.meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def load(self, name: str, tg: TimeTravelEngine) -> Any:
        import pickle
        mp = self.dir / f"{name}.meta.json"
        meta = json.loads(mp.read_text(encoding="utf-8"))
        tg.validate_model(meta, self.spec)
        with open(self.dir / f"{name}.pkl", "rb") as f:
            return pickle.load(f)


def to_numpy(table: pa.Table, cols: list[str]):
    return np.column_stack([table.column(c).to_numpy(zero_copy_only=False).astype(float)
                            for c in cols])


def _xy(table: pa.Table):
    labels = table.column("label").to_numpy(zero_copy_only=False).astype(float)
    X = to_numpy(table, FEATURE_NUMERIC)
    mask = ~np.isnan(labels) & ~np.isnan(X).any(axis=1)
    return X[mask], labels[mask]


# ---------- 基准模型 ----------
def train_benchmark(table: pa.Table) -> dict[str, Any]:
    labels = table.column("label").to_numpy(zero_copy_only=False).astype(float)
    labels = labels[~np.isnan(labels)]
    return {"type": "historical_mean", "mean": float(np.mean(labels)),
            "std": float(np.std(labels)), "n": int(len(labels))}


def benchmark_predict(model: dict[str, Any], table: pa.Table) -> np.ndarray:
    return np.full(table.num_rows, model["mean"])


# ---------- 时序基准（naive：上一年收益）----------
def train_timeseries(table: pa.Table) -> dict[str, Any]:
    """时序基准：用最近一年的横截面均值收益作为下一期预测（AR(1)-naive）。"""
    asof = table.column("asof_date").to_pylist()
    labels = table.column("label").to_numpy(zero_copy_only=False).astype(float)
    # 按 asof 分组，取每个时点的均值，按时间排序
    by_date: dict[str, list[float]] = {}
    for d, y in zip(asof, labels):
        if not np.isnan(y):
            by_date.setdefault(d, []).append(float(y))
    series = [(d, float(np.mean(v))) for d, v in sorted(by_date.items())]
    last = series[-1][1] if series else 0.0
    return {"type": "timeseries_naive", "last_value": last,
            "series": series, "n": len(series)}


def timeseries_predict(model: dict[str, Any], table: pa.Table) -> np.ndarray:
    return np.full(table.num_rows, model["last_value"])


# ---------- 树模型 ----------
def train_lightgbm(table: pa.Table, cfg: dict | None = None):
    cfg = cfg or get_config()["models"]["lightgbm"]
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as e:
        log.warning("LightGBM 不可用，跳过: %s", e)
        return None, {"n": 0, "note": f"lightgbm unavailable: {e}"}
    X, y = _xy(table)
    if len(y) < 3:
        return None, {"n": int(len(y)), "note": "样本不足，跳过"}
    model = lgb.LGBMRegressor(
        n_estimators=cfg["n_estimators"], learning_rate=cfg["learning_rate"],
        num_leaves=cfg["num_leaves"], max_depth=cfg["max_depth"],
        min_child_samples=cfg["min_child_samples"], verbosity=-1,
        random_state=SEED, n_jobs=1,
    )
    model.fit(X, y)
    return model, {"n": int(len(y)), "params": cfg}


def train_xgboost(table: pa.Table, cfg: dict | None = None):
    try:
        import xgboost as xgb
    except (ImportError, OSError) as e:
        log.warning("XGBoost 不可用，跳过: %s", e)
        return None, {"n": 0, "note": f"xgboost unavailable: {e}"}
    X, y = _xy(table)
    if len(y) < 3:
        return None, {"n": int(len(y)), "note": "样本不足，跳过"}
    cfg = cfg or {"max_depth": 4, "learning_rate": 0.05, "n_estimators": 120,
                  "subsample": 0.9, "colsample_bytree": 0.9}
    model = xgb.XGBRegressor(
        max_depth=cfg["max_depth"], learning_rate=cfg["learning_rate"],
        n_estimators=cfg["n_estimators"], subsample=cfg["subsample"],
        colsample_bytree=cfg["colsample_bytree"], random_state=SEED,
        n_jobs=1, verbosity=0,
    )
    model.fit(X, y)
    return model, {"n": int(len(y)), "params": cfg}


def train_catboost(table: pa.Table, cfg: dict | None = None):
    try:
        from catboost import CatBoostRegressor
    except (ImportError, OSError) as e:
        log.warning("CatBoost 不可用，跳过: %s", e)
        return None, {"n": 0, "note": f"catboost unavailable: {e}"}
    X, y = _xy(table)
    if len(y) < 3:
        return None, {"n": int(len(y)), "note": "样本不足，跳过"}
    cfg = cfg or {"depth": 4, "learning_rate": 0.05, "iterations": 150, "l2_leaf_reg": 3}
    model = CatBoostRegressor(
        depth=cfg["depth"], learning_rate=cfg["learning_rate"],
        iterations=cfg["iterations"], l2_leaf_reg=cfg["l2_leaf_reg"],
        random_seed=SEED, thread_count=1, verbose=False,
    )
    model.fit(X, y)
    return model, {"n": int(len(y)), "params": cfg}


# ---------- 可选 PyTorch MLP ----------
def train_torch_mlp(table: pa.Table, cfg: dict | None = None):
    try:
        import torch
    except (ImportError, OSError) as e:
        log.warning("PyTorch 不可用，跳过: %s", e)
        return None, {"n": 0, "note": f"torch unavailable: {e}"}
    X, y = _xy(table)
    if len(y) < 3:
        return None, {"n": int(len(y)), "note": "样本不足，跳过"}
    cfg = cfg or {"hidden": 8, "epochs": 60, "lr": 0.01, "batch_size": 32}
    torch.manual_seed(SEED)
    Xt = torch.tensor(X, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32).view(-1, 1)
    model = torch.nn.Sequential(
        torch.nn.Linear(X.shape[1], cfg["hidden"]), torch.nn.ReLU(),
        torch.nn.Linear(cfg["hidden"], 1),
    )
    opt = torch.optim.Adam(model.parameters(), lr=cfg["lr"])
    loss_fn = torch.nn.MSELoss()
    for _ in range(cfg["epochs"]):
        for i in range(0, len(Xt), cfg["batch_size"]):
            opt.zero_grad()
            loss = loss_fn(model(Xt[i:i+cfg["batch_size"]]), yt[i:i+cfg["batch_size"]])
            loss.backward()
            opt.step()
    return model, {"n": int(len(y)), "params": cfg, "framework": "torch"}


def tree_predict(model, table: pa.Table) -> np.ndarray:
    return np.asarray(model.predict(to_numpy(table, FEATURE_NUMERIC)), dtype=float)


def torch_predict(model, table: pa.Table) -> np.ndarray:
    import torch
    with torch.no_grad():
        return model(torch.tensor(to_numpy(table, FEATURE_NUMERIC), dtype=torch.float32)).numpy().ravel()


def lgbm_predict(model, table: pa.Table) -> np.ndarray:
    return tree_predict(model, table)


# ---------- 统一训练与对比 ----------
TRAINERS = {
    "benchmark": (train_benchmark, benchmark_predict, BENCHMARK_VERSION),
    "timeseries": (train_timeseries, timeseries_predict, TS_VERSION),
    "lightgbm": (train_lightgbm, tree_predict, LGBM_VERSION),
    "xgboost": (train_xgboost, tree_predict, XGB_VERSION),
    "catboost": (train_catboost, tree_predict, CAT_VERSION),
    "torch_mlp": (train_torch_mlp, torch_predict, MLP_VERSION),
}


def train_all(table: pa.Table, registry: ModelRegistry,
              feature_version: str) -> dict[str, Any]:
    """训练全部可用模型并保存，返回 stats 汇总（同一特征/时间边界）。"""
    out = {}
    import time
    for name, (trainer, _, version) in TRAINERS.items():
        t0 = time.perf_counter()
        try:
            if name == "benchmark":
                model = trainer(table)
                stats = model
            else:
                model, stats = trainer(table)
            if model is None:
                out[name] = {"status": "skipped", **stats}
                continue
            stats = dict(stats)
            stats.update({"version": version, "feature_version": feature_version,
                          "trained_to": registry.spec.cutoff_date.isoformat(),
                          "training_start": registry.spec.training_start_date.isoformat(),
                          "snapshot_id": registry.spec.knowledge_snapshot_id})
            registry.save(name, model, stats)
            out[name] = {"status": "trained", "stats": stats,
                         "elapsed_seconds": round(time.perf_counter() - t0, 3)}
        except Exception as e:
            log.exception("模型 %s 训练失败", name)
            out[name] = {"status": "failed", "error": str(e)}
    return out
