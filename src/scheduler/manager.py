"""v0.7.0 P14/P15：资源管理器 + 研究调度器（低内存个人工作站）。"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

import psutil


@dataclass
class ResourcePolicy:
    max_parallel_experiments: int = 1
    max_cpu_threads_per_experiment: int = 2
    max_memory_fraction: float = 0.60
    allow_gpu: bool = False
    max_experiment_runtime_minutes: int = 120
    max_hypotheses_per_day: int = 20
    max_formal_experiments_per_day: int = 5


class ResourceManager:
    def __init__(self, policy: ResourcePolicy | None = None):
        self.policy = policy or ResourcePolicy()
        self.proc = psutil.Process()

    def snapshot(self) -> dict[str, Any]:
        m = self.proc.memory_info()
        return {"rss_mb": round(m.rss / 1024 / 1024, 2),
                "cpu_percent": self.proc.cpu_percent(interval=0.1),
                "disk_free_gb": round(psutil.disk_usage(".").free / 1024**3, 2)}

    def within_budget(self) -> bool:
        s = self.snapshot()
        total = psutil.virtual_memory().total
        return (s["rss_mb"] / 1024) < self.policy.max_memory_fraction * (total / 1024**3)

    def run_budgeted(self, fn: Callable, *args, **kwargs) -> dict[str, Any]:
        if not self.within_budget():
            return {"ok": False, "reason": "memory_budget_exceeded"}
        t0 = time.perf_counter()
        out = fn(*args, **kwargs)
        return {"ok": True, "result": out,
                "elapsed_seconds": round(time.perf_counter() - t0, 4),
                "resources": self.snapshot()}


class ResearchScheduler:
    def __init__(self, manager: ResourceManager | None = None):
        self.manager = manager or ResourceManager()
        self.policy = self.manager.policy
        self.queue: list[dict[str, Any]] = []
        self.completed: list[str] = []
        self.rejected: list[str] = []

    def submit(self, hypothesis: dict[str, Any]) -> str:
        if len(self.queue) >= self.policy.max_hypotheses_per_day:
            self.rejected.append(hypothesis.get("hypothesis_id", "unknown"))
            return "REJECTED_BUDGET"
        self.queue.append(hypothesis)
        return "QUEUED"

    def run_next(self, fn: Callable) -> dict[str, Any]:
        if not self.queue:
            return {"status": "EMPTY"}
        if len(self.completed) >= self.policy.max_formal_experiments_per_day:
            return {"status": "DAILY_BUDGET_REACHED"}
        h = self.queue.pop(0)
        out = self.manager.run_budgeted(fn, h)
        self.completed.append(h.get("hypothesis_id", "unknown"))
        return {"status": "RUN", "hypothesis_id": h.get("hypothesis_id"), **out}
