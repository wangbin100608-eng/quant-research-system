"""v0.3.0 P0-4 验证报告：7 期限样本 × 5 种 split × 多模型 vs 基准。

输出 data/reports/validation_report_v03.json：
  * 每期限样本数、每 split 的 train/test/purged
  * 模型 vs benchmark 对比（含统计证据不足判定）
  * 独立最终测试集（最后一折）不参与超参选择
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pyarrow as pa
from sklearn.linear_model import LinearRegression
from sklearn.dummy import DummyRegressor
from lightgbm import LGBMRegressor

from quant.horizons import HORIZONS, build_horizon_samples
from quant.utils import ROOT, resolve
from quant.logging_util import log
from quant.validation import (expanding_window, rolling_window, walk_forward,
                              purged_embargo_split, cross_validate)

FEATURES = ["ret_1d", "ret_20d", "ret_60d", "vol_20d", "momentum_60d"]


def model_factories():
    return {
        "benchmark_mean": lambda: DummyRegressor(strategy="mean"),
        "linear": lambda: LinearRegression(),
        "lightgbm": lambda: LGBMRegressor(n_estimators=80, num_leaves=15,
                                          learning_rate=0.05, verbosity=-1,
                                          random_state=20000101, n_jobs=1),
    }


def main():
    raw = resolve("data/raw/raw_v03.parquet")
    if not raw.exists():
        raise SystemExit("先运行 fetch_v03.py")
    samples = build_horizon_samples(raw, "data/features", step=10)
    report = {"generated_at": datetime.utcnow().isoformat(),
              "data_source": "real (raw_v03.parquet)", "synthetic": False,
              "validation_methods": ["expanding_window", "rolling_window",
                                     "walk_forward", "purged_embargo"],
              "independent_final_test": "每个 split 的最后一折仅评估，不参与超参选择",
              "horizons": {}}

    for h, table in samples.items():
        if table.num_rows < 60:
            report["horizons"][h] = {"n": table.num_rows,
                                     "insufficient_evidence": True,
                                     "note": "样本不足，停止验证"}
            continue
        split_fns = {
            "expanding_window": lambda t: expanding_window(t, n_splits=4, test_size=40, embargo=0),
            "rolling_window": lambda t: rolling_window(t, n_splits=4, train_size=200,
                                                       test_size=40, embargo=0),
            "walk_forward": lambda t: walk_forward(t, n_splits=4, test_size=21, purge=True),
            "purged_embargo": lambda t: purged_embargo_split(t, n_splits=4,
                                                            embargo_days=5, test_size=40),
        }
        hreport = {"n_samples": table.num_rows, "target_definition": f"future_{h}_return",
                   "splits": {}}
        for sname, sfn in split_fns.items():
            splits = sfn(table)
            # 独立最终测试：最后一折只在最终评估时报告
            final = splits[-1]
            dev = splits[:-1]
            methods = {}
            for mname, factory in model_factories().items():
                dev_res = cross_validate(table, factory, dev, FEATURES)
                final_res = cross_validate(table, factory, [final], FEATURES)
                methods[mname] = {
                    "dev_mean_mae": dev_res["summary"]["mean_mae"],
                    "dev_mean_rank_ic": dev_res["summary"]["mean_rank_ic"],
                    "final_test_mae": final_res["folds"][0]["metrics"]["mae"]
                                      if final_res["folds"] and not final_res["folds"][0].get("skipped") else None,
                    "final_test_n": final_res["folds"][0]["test_n"]
                                    if final_res["folds"] and not final_res["folds"][0].get("skipped") else 0,
                }
            best = min((v for v in methods.values() if v["dev_mean_mae"] is not None),
                       key=lambda x: x["dev_mean_mae"], default=None)
            bench = methods.get("benchmark_mean", {}).get("final_test_mae")
            winner = methods.get("linear", {}).get("final_test_mae")
            hreport["splits"][sname] = {
                "n_splits": len(splits),
                "purged_total": sum(s.purged for s in splits),
                "methods": methods,
                "best_dev_model": best,
                "beats_benchmark_on_final": (winner < bench) if (winner and bench) else None,
                "final_test_range": [final.test_start, final.test_end],
            }
        report["horizons"][h] = hreport
        log.info("horizon %s 验证完成: %d 样本", h, table.num_rows)

    out = resolve("data/reports/validation_report_v03.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "horizons": {h: v.get("n_samples", v.get("n")) for h, v in report["horizons"].items()},
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
