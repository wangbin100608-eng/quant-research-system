"""特征工程：所有特征只使用 available_time <= cutoff 的数据，且按快照版本缓存。

特征版本 feature_v1：
  - trailing_mean_return_1y / 2y：历史年度收益均值
  - trailing_vol_1y：历史日收益波动率年化
  - momentum_6m：近 6 个月动量
  - avg_turnover：平均成交量（对数）
  - fundamental_eps_latest：最近一期已披露 EPS
特征按 (symbol, asof_date) 计算，特征缓存写入对应快照目录，防止跨年污染。
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .contracts import PredictionSpec, parse_date
from .utils import resolve
from .logging_util import log

FEATURE_VERSION = "feature_v1"
FEATURE_COLUMNS = [
    "symbol", "market", "asof_date", "trailing_mean_return_1y",
    "trailing_mean_return_2y", "trailing_vol_1y", "momentum_6m",
    "avg_turnover_log", "eps_latest", "synthetic",
]


def _load_clean(snapshot_path) -> pa.Table:
    return pq.read_table(str(snapshot_path / "clean_data.parquet"))


def _rows_by_symbol(table: pa.Table) -> dict[str, list[dict[str, Any]]]:
    syms = table.column("symbol").to_pylist()
    doms = table.column("domain").to_pylist()
    times = table.column("available_time").to_pylist()
    pays = table.column("payload").to_pylist()
    mkts = table.column("market").to_pylist()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s, d, t, p, m in zip(syms, doms, times, pays, mkts):
        try:
            payload = json.loads(p) if p else {}
        except Exception:
            payload = {}
        out[s].append({"domain": d, "date": parse_date(t), "payload": payload, "market": m})
    return out


def annual_returns(prices: list[dict[str, Any]], end_year: int) -> dict[int, float]:
    """按日收盘价计算年度收益（首末日）。"""
    by_year: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for r in prices:
        d = r["date"]
        px = r["payload"].get("close")
        if d and px is not None:
            by_year[d.year].append((d, float(px)))
    out = {}
    for y, pts in by_year.items():
        pts.sort()
        if len(pts) >= 2 and pts[0][1] > 0:
            out[y] = pts[-1][1] / pts[0][1] - 1.0
    return out


def build_features(snapshot_path, spec: PredictionSpec) -> pa.Table:
    """为训练样本生成特征：对每个 symbol，在每年末 asof 生成 t 年特征，标签为 t+1 年收益。"""
    table = _load_clean(snapshot_path)
    grouped = _rows_by_symbol(table)
    feature_rows = []
    label_rows = []
    for sym, rows in grouped.items():
        prices = [r for r in rows if r["domain"] == "price"]
        funds = [r for r in rows if r["domain"] == "fundamental"]
        rets = annual_returns(prices, spec.cutoff_date.year)
        years = sorted(rets)
        mkt = prices[0]["market"] if prices else "UNKNOWN"
        for y in years:
            if y < spec.training_start_date.year:
                continue
            hist = [rets[k] for k in years if k <= y]
            if not hist:
                continue
            m1 = sum(hist[-1:]) / len(hist[-1:])
            m2 = sum(hist[-2:]) / len(hist[-2:])
            # 波动率：当年日收益
            daily = sorted([r for r in prices if r["date"] and r["date"].year == y],
                           key=lambda r: r["date"])
            vol = 0.0
            if len(daily) > 2:
                dr = []
                for a, b in zip(daily, daily[1:]):
                    p0, p1 = a["payload"].get("close"), b["payload"].get("close")
                    if p0 and p1:
                        dr.append(math.log(float(p1) / float(p0)))
                if dr:
                    mean = sum(dr) / len(dr)
                    vol = math.sqrt(sum((x - mean) ** 2 for x in dr) / len(dr)) * math.sqrt(252)
            # 6 个月动量
            h2 = [r for r in prices if r["date"] and
                  date(y, 7, 1) <= r["date"] <= date(y, 12, 31)]
            momentum = 0.0
            if len(h2) >= 2:
                h2.sort(key=lambda r: r["date"])
                p0, p1 = h2[0]["payload"].get("close"), h2[-1]["payload"].get("close")
                if p0 and p1:
                    momentum = float(p1) / float(p0) - 1.0
            turnover = [r["payload"].get("volume") for r in prices
                        if r["date"] and r["date"].year == y and r["payload"].get("volume")]
            avg_log = math.log(sum(turnover) / len(turnover) + 1) if turnover else 0.0
            # 最新已披露 EPS（<= 年末）
            eps = 0.0
            cand = [f for f in funds if f["date"] and f["date"] <= date(y, 12, 31)
                    and f["payload"].get("eps") is not None]
            if cand:
                cand.sort(key=lambda r: r["date"])
                eps = float(cand[-1]["payload"]["eps"])
            feature_rows.append({
                "symbol": sym, "market": mkt, "asof_date": date(y, 12, 31).isoformat(),
                "trailing_mean_return_1y": m1, "trailing_mean_return_2y": m2,
                "trailing_vol_1y": vol, "momentum_6m": momentum,
                "avg_turnover_log": avg_log, "eps_latest": eps,
                "synthetic": any(r["payload"].get("synthetic") for r in rows),
                "label": rets.get(y + 1),  # 下一年收益（训练标签）
            })
    schema = pa.schema([(c, pa.float64()) for c in FEATURE_COLUMNS if c not in
                        ("symbol", "market", "asof_date", "synthetic")])
    schema = pa.schema([
        ("symbol", pa.string()), ("market", pa.string()), ("asof_date", pa.string()),
        ("trailing_mean_return_1y", pa.float64()), ("trailing_mean_return_2y", pa.float64()),
        ("trailing_vol_1y", pa.float64()), ("momentum_6m", pa.float64()),
        ("avg_turnover_log", pa.float64()), ("eps_latest", pa.float64()),
        ("synthetic", pa.bool_()), ("label", pa.float64()),
    ])
    ft = pa.Table.from_pylist(feature_rows, schema=schema)
    out_path = resolve(str(snapshot_path / "features.parquet"))
    pq.write_table(ft, str(out_path), compression="zstd")
    log.info("特征 %s 生成 %d 行 (feature_version=%s)",
             spec.knowledge_snapshot_id, ft.num_rows, FEATURE_VERSION)
    return ft
