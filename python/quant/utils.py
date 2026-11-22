"""配置加载、路径解析、资源计量（内存峰值/耗时/磁盘）。

v0.2.0 修复 mem_peak=0.0：
  * tracemalloc 嵌套调用会互相 stop，改为引用计数，只有最外层 start/stop；
  * 同时记录 ru_maxrss（进程级 RSS 峰值，替代/补充 tracemalloc 峰值），
    即使嵌套或第三方 C 扩展（LightGBM/XGBoost）分配内存也能反映。
"""
from __future__ import annotations

import os
import resource
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(os.environ.get("QUANT_ROOT", str(Path(__file__).resolve().parents[2])))
_TRACEMALLOC_DEPTH = 0


def load_yaml(rel: str) -> dict[str, Any]:
    p = ROOT / rel
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_config() -> dict[str, Any]:
    return load_yaml("config/config.yaml")


def resolve(rel: str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else ROOT / p


def _rss_mb() -> float:
    # Linux ru_maxrss 单位 KB
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)


@contextmanager
def measure(task_name: str) -> Iterator[dict[str, Any]]:
    """记录任务的 wall time、tracemalloc 峰值与进程 RSS 峰值（嵌套安全）。"""
    global _TRACEMALLOC_DEPTH
    t0 = time.perf_counter()
    rss0 = _rss_mb()
    stats: dict[str, Any] = {"task": task_name}
    started_here = False
    if _TRACEMALLOC_DEPTH == 0 and not tracemalloc.is_tracing():
        tracemalloc.start()
        started_here = True
    _TRACEMALLOC_DEPTH += 1
    try:
        yield stats
    finally:
        _TRACEMALLOC_DEPTH -= 1
        elapsed = time.perf_counter() - t0
        peak_mb = 0.0
        if tracemalloc.is_tracing():
            _, peak = tracemalloc.get_traced_memory()
            peak_mb = round(peak / 1024 / 1024, 2)
        # 只有最外层停止，避免内层 stop 清零外层
        if started_here and _TRACEMALLOC_DEPTH == 0:
            tracemalloc.stop()
        stats["elapsed_seconds"] = round(elapsed, 4)
        stats["mem_peak_mb"] = peak_mb
        stats["rss_mb"] = _rss_mb()
        stats["rss_delta_mb"] = round(stats["rss_mb"] - rss0, 2)
