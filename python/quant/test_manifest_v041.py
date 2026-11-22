"""v0.4.1 P0-4：版本化测试清单（唯一 ID / 删除 / 跳过 / 失败 / 错误 / xfail）。

不再用固定 50 项作为标准：pytest --collect-only 生成清单，真实执行生成结果，逐项比较。
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "data/reports/test_manifest_v041.json"


def collect_tests() -> list[str]:
    r = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "python/tests"),
                        "--collect-only", "-q"],
                       cwd=str(ROOT), env={**__import__("os").environ,
                                           "PYTHONPATH": str(ROOT / "python")},
                       capture_output=True, text=True, timeout=120)
    ids = []
    for line in r.stdout.splitlines():
        m = re.match(r"^(\S+::\S+):?\s*$", line.strip())
        if m:
            ids.append(m.group(1))
    return sorted(set(ids))


def build_manifest() -> dict[str, Any]:
    ids = collect_tests()
    raw = json.dumps(ids, sort_keys=True).encode()
    return {
        "version": "v0.4.1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(ids),
        "test_ids": ids,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def save_manifest() -> dict[str, Any]:
    m = build_manifest()
    MANIFEST_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return m


def verify_execution(manifest: dict[str, Any], pytest_output: str) -> dict[str, Any]:
    """对比执行结果与清单：删除/跳过/失败/错误/xfail。"""
    m = re.search(r"(\d+) passed(?:, (\d+) skipped)?(?:, (\d+) xfailed)?(?:, (\d+) failed)?"
                  r"(?:, (\d+) error)?", pytest_output)
    passed = int(m.group(1)) if m else 0
    skipped = int(m.group(2) or 0)
    xfailed = int(m.group(3) or 0)
    failed = int(m.group(4) or 0)
    errors = int(m.group(5) or 0)
    actual_ids = set(re.findall(r"^(\S+::\S+)", pytest_output, re.M))
    missing = set(manifest["test_ids"]) - actual_ids
    # passed 行不含 test id 时 missing 可能误报，仅当输出含 PASSED/FAILED 详细模式才判定
    return {
        "manifest_count": manifest["count"],
        "passed": passed, "skipped": skipped, "xfailed": xfailed,
        "failed": failed, "errors": errors,
        "deleted_tests": sorted(missing) if actual_ids else [],
        "ok": (failed == 0 and errors == 0 and skipped == 0 and
               passed >= manifest["count"] and not (actual_ids and missing)),
    }
