"""v0.7 P16：六组模型基线实验（CN_A/US × 1d/5d/20d × 简单基准）。

只做基准配置实验，不宣称稳定预测能力。结果写入 data/reports/model_baselines_v07.json。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from sklearn.dummy import DummyRegressor
from sklearn.linear_model import LinearRegression, Ridge

from quant.utils import ROOT, resolve
from quant.validation import compute_metrics
from quant.stats_v060 import evidence_level

GROUPS = [(m, h) for m in ("CN_A", "US") for h in ("1d", "5d", "20d")]
MODELS = {
    "naive_mean": lambda: DummyRegressor(strategy="mean"),
    "linear": lambda: LinearRegression(),
    "ridge": lambda: Ridge(alpha=1.0, random_state=20000101),
}
FEATURES = ["ret_1d", "ret_20d", "ret_60d", "vol_20d", "momentum_60d"]


def main() -> dict:
    samples_dir = resolve("data/features")
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "version": "v0.7.0", "data_source": "real raw_v03",
              "note": "样本量小的结果仅作基线记录，不宣称稳定预测能力",
              "groups": {}}
    for market, h in GROUPS:
        key = f"{market}_{h}"
        p = samples_dir / f"samples_{h}.parquet"
        if not p.exists():
            report["groups"][key] = {"status": "BLOCKED", "reason": "samples missing"}
            continue
        t = pq.read_table(str(p))
        markets = t.column("market").to_pylist()
        idx = [i for i, m in enumerate(markets) if m == market]
        t = t.take(__import__("pyarrow").array(idx, type=__import__("pyarrow").int64()))
        X = np.column_stack([t.column(c).to_numpy(zero_copy_only=False).astype(float)
                             for c in FEATURES])
        y = t.column("target_return").to_numpy(zero_copy_only=False).astype(float)
        mask = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
        X, y = X[mask], y[mask]
        if len(y) < 30:
            report["groups"][key] = {"status": "BLOCKED",
                                     "reason": f"insufficient samples n={len(y)}",
                                     "n": int(len(y))}
            continue
        split = int(len(y) * 0.7)
        res = {}
        for name, factory in MODELS.items():
            mdl = factory()
            mdl.fit(X[:split], y[:split])
            pred = mdl.predict(X[split:])
            met = compute_metrics(pred, y[split:])
            met["evidence"] = evidence_level(int(met.get("n", 0)))
            res[name] = met
        report["groups"][key] = {"status": "PASS", "n": int(len(y)),
                                 "train_n": split, "test_n": len(y) - split,
                                 "models": res}
    out = resolve("data/reports/model_baselines_v07.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


if __name__ == "__main__":
    r = main()
    print(json.dumps({k: v["status"] for k, v in r["groups"].items()},
                     ensure_ascii=False, indent=2))
