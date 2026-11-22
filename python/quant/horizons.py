"""v0.3.0 P0-3：多期限训练样本生成器。

每个 horizon 有独立目标变量：
  1d/5d/20d：未来 N 个交易日的收益
  1m/3m/6m/1y：未来 21/63/126/252 个交易日的收益

严格防重叠泄漏：
  * 样本 feature_cutoff = 特征计算时的最后可得日（t）
  * 样本 target_end = t + horizon 天（目标区间右端）
  * 若某训练样本的 [t, target_end] 与测试区 [test_start, test_end] 重叠，
    该样本必须被 purge（在 validation.py 的 PurgedSplit 中实现）。
  * 本模块为每个样本记录 feature_time / target_start / target_end，供 split 使用。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .logging_util import log
from .utils import resolve

# horizon -> 交易日数（近似：1月=21，3月=63，6月=126，1年=252）
HORIZONS = {
    "1d": 1, "5d": 5, "20d": 20,
    "1m": 21, "3m": 63, "6m": 126, "1y": 252,
}

SCHEMA = pa.schema([
    ("symbol", pa.string()), ("market", pa.string()),
    ("feature_time", pa.string()), ("target_start", pa.string()),
    ("target_end", pa.string()), ("horizon", pa.string()),
    ("feature_window_days", pa.int32()),
    ("ret_1d", pa.float64()), ("ret_20d", pa.float64()), ("ret_60d", pa.float64()),
    ("vol_20d", pa.float64()), ("momentum_60d", pa.float64()),
    ("target_return", pa.float64()), ("target_vol", pa.float64()),
    ("source", pa.string()), ("synthetic", pa.bool_()),
])


def build_horizon_samples(raw_path: str | Path, out_dir: str | Path | None = None,
                          step: int = 5) -> dict[str, pa.Table]:
    """从真实日线生成样本；step 控制采样间隔（降低重叠密度）。

    返回 {horizon: pa.Table}，并写 data/features/samples_{horizon}.parquet。
    """
    t = pq.read_table(str(raw_path), columns=["symbol", "market", "domain",
                                              "available_time", "payload", "source"])
    prices: dict[str, list[tuple[date, float]]] = {}
    markets: dict[str, str] = {}
    srcs: dict[str, str] = {}
    syms = t.column("symbol").to_pylist()
    mkts = t.column("market").to_pylist()
    doms = t.column("domain").to_pylist()
    times = t.column("available_time").to_pylist()
    pays = t.column("payload").to_pylist()
    src_col = t.column("source").to_pylist()
    import json as _json
    for s, m, dom, tm, pay, src in zip(syms, mkts, doms, times, pays, src_col):
        if dom != "price" or not tm:
            continue
        try:
            p = _json.loads(pay or "{}")
            px = p.get("close")
            if px is None:
                continue
        except Exception:
            continue
        d = date.fromisoformat(str(tm)[:10])
        prices.setdefault(s, []).append((d, float(px)))
        markets[s] = m
        srcs[s] = src

    outputs: dict[str, pa.Table] = {}
    for h, n_days in HORIZONS.items():
        rows = []
        for sym, pts in prices.items():
            pts = sorted(set(pts))
            closes = [p for _, p in pts]
            dates_ = [d for d, _ in pts]
            for i in range(60, len(pts) - n_days, step):
                ft = dates_[i]
                target_end = dates_[i + n_days]
                window = closes[max(0, i - 60):i + 1]
                ret_1d = closes[i] / closes[i - 1] - 1 if i >= 1 else 0.0
                ret_20d = closes[i] / closes[i - 20] - 1 if i >= 20 else 0.0
                ret_60d = closes[i] / closes[i - 60] - 1 if i >= 60 else 0.0
                daily = [closes[j] / closes[j - 1] - 1 for j in range(max(1, i - 20), i + 1)]
                vol = float(np.std(daily) * np.sqrt(252)) if daily else 0.0
                future = closes[i + 1:i + n_days + 1]
                target = closes[i + n_days] / closes[i] - 1
                tvol = float(np.std([future[j] / future[j - 1] - 1
                                     for j in range(1, len(future))]) * np.sqrt(252)) if len(future) > 1 else 0.0
                rows.append({
                    "symbol": sym, "market": markets[sym],
                    "feature_time": ft.isoformat(),
                    "target_start": dates_[i + 1].isoformat(),
                    "target_end": target_end.isoformat(),
                    "horizon": h, "feature_window_days": 60,
                    "ret_1d": ret_1d, "ret_20d": ret_20d, "ret_60d": ret_60d,
                    "vol_20d": vol, "momentum_60d": ret_60d,
                    "target_return": target, "target_vol": tvol,
                    "source": srcs[sym], "synthetic": False,
                })
        table = pa.Table.from_pylist(rows, schema=SCHEMA)
        outputs[h] = table
        if out_dir is not None:
            p = resolve(f"{out_dir}/samples_{h}.parquet")
            p.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, str(p), compression="zstd")
        log.info("horizon %s: %d 样本", h, table.num_rows)
    return outputs
