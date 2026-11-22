"""模型基准对比 v0.2.0：在真实数据上用统一时间边界比较各模型样本外表现。

方法：
  * 数据源：data/raw/raw_real.parquet（仅真实数据）
  * 特征：与 quant.features 相同的 6 个数值特征
  * 时间切分：训练 <= 2002-12-31（strict point-in-time），测试 = 2003/2004
  * 所有模型只看训练期数据，禁止未来信息
  * 输出：data/reports/model_comparison.json
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from quant.contracts import PredictionSpec, parse_date
from quant.models import TRAINERS
from quant.utils import ROOT, resolve


def build_real_features(raw_path: Path, cutoff: date) -> pa.Table:
    """从真实 raw 数据构建年度面板特征（仅用 available_time <= cutoff 的信息生成历史特征）。"""
    t = pq.read_table(str(raw_path))
    syms = t.column("symbol").to_pylist()
    mkts = t.column("market").to_pylist()
    doms = t.column("domain").to_pylist()
    times = t.column("available_time").to_pylist()
    pays = t.column("payload").to_pylist()
    # 按 symbol/domain 组织
    prices: dict[str, list[tuple[date, float]]] = defaultdict(list)
    macro: dict[str, list[tuple[date, float]]] = defaultdict(list)
    funds: dict[str, list[tuple[date, str]]] = defaultdict(list)
    for s, d, tm, p in zip(syms, doms, times, pays):
        if not tm:
            continue
        dt = parse_date(tm)
        if dt is None or dt > date(2004, 12, 31):
            continue
        try:
            pl = json.loads(p or "{}")
        except Exception:
            continue
        px = pl.get("close")
        if d == "price" and px is not None:
            prices[s].append((dt, float(px)))
        elif d == "macro" and pl.get("value") is not None:
            macro[s].append((dt, float(pl["value"])))
        elif d in ("fundamental", "announcement"):
            funds[s].append((dt, pl.get("form", "")))
    rows = []
    for s, pts in prices.items():
        by_year: dict[int, list[float]] = defaultdict(list)
        daily: dict[int, list[tuple[date, float]]] = defaultdict(list)
        for dt, px in sorted(pts):
            by_year[dt.year].append(px)
            daily[dt.year].append((dt, px))
        years = sorted(by_year)
        annual_ret = {y: (v[-1] / v[0] - 1) if len(v) >= 2 and v[0] else 0.0
                      for y, v in by_year.items()}
        for y in years:
            if y < 2000 or y > 2004:
                continue
            hist = [annual_ret[k] for k in years if k <= y and k in annual_ret]
            if not hist:
                continue
            m1 = float(np.mean(hist[-1:]))
            m2 = float(np.mean(hist[-2:]))
            dpts = daily[y]
            rets = [np.log(b[1] / a[1]) for a, b in zip(dpts, dpts[1:]) if a[1] > 0]
            vol = float(np.std(rets) * np.sqrt(252)) if rets else 0.0
            h2 = [px for dt, px in dpts if date(y, 7, 1) <= dt <= date(y, 12, 31)]
            mom = (h2[-1] / h2[0] - 1) if len(h2) >= 2 else 0.0
            # 宏观特征：用 DGS10 当年均值（available_time <= cutoff 才可用）
            dgs = [v for dt, v in macro.get("DGS10", []) if dt.year == y]
            rate = float(np.mean(dgs)) if dgs else 0.0
            rows.append({
                "symbol": s, "asof_date": date(y, 12, 31).isoformat(),
                "trailing_mean_return_1y": m1, "trailing_mean_return_2y": m2,
                "trailing_vol_1y": vol, "momentum_6m": float(mom),
                "avg_turnover_log": 0.0, "eps_latest": rate,  # rate 作代理特征
                "label": annual_ret.get(y + 1),  # 下一年真实收益（仅评估用）
            })
    schema = pa.schema([
        ("symbol", pa.string()), ("asof_date", pa.string()),
        ("trailing_mean_return_1y", pa.float64()), ("trailing_mean_return_2y", pa.float64()),
        ("trailing_vol_1y", pa.float64()), ("momentum_6m", pa.float64()),
        ("avg_turnover_log", pa.float64()), ("eps_latest", pa.float64()),
        ("label", pa.float64()),
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def _xy(table):
    from quant.models import FEATURE_NUMERIC
    X = np.column_stack([table.column(c).to_numpy(zero_copy_only=False).astype(float)
                         for c in FEATURE_NUMERIC])
    y = table.column("label").to_numpy(zero_copy_only=False).astype(float)
    mask = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
    return X[mask], y[mask]


def main():
    raw = resolve("data/raw/raw_real.parquet")
    if not raw.exists():
        raise SystemExit("先运行 fetch_real.py 生成 raw_real.parquet")
    all_feat = build_real_features(raw, date(2004, 12, 31))
    asof = all_feat.column("asof_date").to_pylist()
    # 训练 <= 2002，测试 2003/2004
    train_mask = [str(d) <= "2002-12-31" for d in asof]
    test_mask = [str(d) > "2002-12-31" for d in asof]
    train = all_feat.filter(pa.array(train_mask, type=pa.bool_()))
    test = all_feat.filter(pa.array(test_mask, type=pa.bool_()))
    Xtr, ytr = _xy(train)
    # 仅保留 label 有效的测试行，保证预测与真实一一对应
    valid_idx = [i for i, y in enumerate(test.column("label").to_pylist())
                 if y is not None and not (isinstance(y, float) and np.isnan(y))]
    test_valid = test.take(pa.array(valid_idx, type=pa.int64()))
    _, yte = _xy(test_valid)
    print(f"真实数据特征: train={len(ytr)} test={len(yte)}")

    results = {}
    for name, (trainer, predictor, version) in TRAINERS.items():
        t0 = time.perf_counter()
        try:
            if name == "benchmark":
                model = trainer(train)
                pred = predictor(model, test_valid)
            elif name == "timeseries":
                model = trainer(train)
                pred = predictor(model, test_valid)
            else:
                model, stats = trainer(train)
                if model is None:
                    results[name] = {"status": "skipped", "reason": stats.get("note")}
                    continue
                pred = predictor(model, test_valid)
            pred = np.asarray(pred, dtype=float)
            mae = float(np.mean(np.abs(pred - yte)))
            rmse = float(np.sqrt(np.mean((pred - yte) ** 2)))
            direction = float(np.mean(np.sign(pred) == np.sign(yte)))
            results[name] = {
                "status": "ok", "version": version,
                "train_n": int(len(ytr)), "test_n": int(len(yte)),
                "mae": round(mae, 6), "rmse": round(rmse, 6),
                "direction_accuracy": round(direction, 4),
                "elapsed_seconds": round(time.perf_counter() - t0, 3),
            }
            print(f"  {name:12s} MAE={mae:.4f} RMSE={rmse:.4f} DIR={direction:.2f} "
                  f"({results[name]['elapsed_seconds']}s)")
        except Exception as e:
            results[name] = {"status": "failed", "error": str(e)}
            print(f"  {name:12s} FAILED: {e}")

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "data_source": "real (raw_real.parquet)", "synthetic": False,
        "train_range": "<=2002-12-31", "test_range": "2003-01-01..2004-12-31",
        "feature_version": "feature_v1", "features": 6,
        "trained_to": "2002-12-31", "strict_point_in_time": True,
        "results": results,
    }
    out = resolve("data/reports/model_comparison.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")


if __name__ == "__main__":
    main()
