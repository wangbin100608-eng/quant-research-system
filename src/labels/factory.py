"""v0.7.0 P7：Label Factory（复用 horizons 目标边界，加 label_id/version）。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from quant.horizons import HORIZONS, build_horizon_samples
from quant.timestamps_v04 import cutoff_for_year

LABEL_VERSION = "label_v07"


def build_labels(raw_path: str | Path, out_dir: str | Path = "data/labels") -> dict[str, pa.Table]:
    """生成 1d/5d/20d 标签，继承 feature_time/target_start/target_end。"""
    tables = build_horizon_samples(raw_path, out_dir=None, step=5)
    out = {}
    for h in ("1d", "5d", "20d"):
        t = tables[h]
        ids = []
        for sym, ft, te in zip(t.column("symbol").to_pylist(),
                               t.column("feature_time").to_pylist(),
                               t.column("target_end").to_pylist()):
            key = f"{sym}|{ft}|{h}|{te}"
            ids.append("LBL_" + hashlib.sha256(key.encode()).hexdigest()[:16])
        t = t.append_column("label_id", pa.array(ids, type=pa.string()))
        t = t.append_column("label_version", pa.array([LABEL_VERSION] * t.num_rows,
                                                      type=pa.string()))
        # 未成熟标签（target_end > 当前可用截止）不得用于训练：记录字段，交由 TimeGuard 门禁
        p = Path(out_dir) / f"labels_{h}.parquet"
        p.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(t, str(p), compression="zstd")
        out[h] = t
    return out
