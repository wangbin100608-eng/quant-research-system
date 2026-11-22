"""v0.4.1：独立报告验证器（P0-2）。

验证报告的：结构、版本、Git Commit、执行时间、测试总数、PASS/FAIL 数量、报告哈希。
任何篡改都必须被拒绝。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FIELDS = {"version", "generated_at", "commit", "pytest",
                   "summary", "report_sha256"}


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "unknown"


def compute_report_sha(report: dict[str, Any]) -> str:
    """对除 report_sha256 外的规范化内容算哈希。"""
    payload = {k: v for k, v in report.items() if k != "report_sha256"}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class ReportVerifierV041:
    def __init__(self, expected_version: str = "v0.4.1",
                 expected_commit: str | None = None,
                 expected_counts: dict[str, int] | None = None,
                 min_generated_at: str | None = None):
        self.expected_version = expected_version
        self.expected_commit = expected_commit or _git_commit()
        self.expected_counts = expected_counts or {}
        self.min_generated_at = min_generated_at

    def verify(self, report_path: str | Path) -> tuple[bool, list[str]]:
        errors: list[str] = []
        p = Path(report_path)
        if not p.exists():
            return False, ["report missing"]
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            return False, [f"invalid json: {e}"]
        missing = REQUIRED_FIELDS - set(d)
        if missing:
            errors.append(f"missing fields: {sorted(missing)}")
        if d.get("version") != self.expected_version:
            errors.append(f"version mismatch: {d.get('version')} != {self.expected_version}")
        if d.get("commit") != self.expected_commit:
            errors.append(f"commit mismatch: {str(d.get('commit'))[:12]}")
        gen = d.get("generated_at", "")
        try:
            t = datetime.fromisoformat(gen.replace("Z", "+00:00"))
            if t.tzinfo is None:
                errors.append("generated_at not timezone-aware")
            if self.min_generated_at and t < datetime.fromisoformat(
                    self.min_generated_at.replace("Z", "+00:00")):
                errors.append("generated_at too old")
        except Exception:
            errors.append(f"invalid generated_at: {gen}")
        # 数量校验
        summary = d.get("summary", {}) if isinstance(d.get("summary"), dict) else {}
        for key, expected in self.expected_counts.items():
            if summary.get(key) != expected:
                errors.append(f"{key} mismatch: {summary.get(key)} != {expected}")
        # PASS/FAIL 数量一致性
        checks = d.get("checks")
        if isinstance(checks, list):
            npass = sum(1 for c in checks if c.get("result") == "PASS")
            nfail = sum(1 for c in checks if c.get("result") == "FAIL")
            if npass + nfail != len(checks):
                errors.append("PASS/FAIL counts do not sum to total")
            if summary.get("passed") is not None and summary.get("passed") != npass:
                errors.append("summary.passed mismatch actual PASS")
        # 报告哈希
        actual_sha = compute_report_sha(d)
        if d.get("report_sha256") and d["report_sha256"] != actual_sha:
            errors.append("report_sha256 mismatch (tampered)")
        return (not errors), errors
