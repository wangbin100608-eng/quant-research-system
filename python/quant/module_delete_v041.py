"""v0.4.1 P0-1：真正的模块删除验收（完整临时副本 + 独立进程）。

流程：复制 python/quant 到临时目录 → 删除目标模块 → 在独立进程中运行"模块加载检查脚本"
（该脚本尝试 import 所有核心模块，任一失败即 exit 1）→ 确认退出码非零 → 恢复 → 确认 exit 0。
原始项目只读；不使用 find_spec 或文件路径替代真实导入。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CORE_MODULES = [
    "quant.timeguard", "quant.timeguard_reader", "quant.timestamps_v04",
    "quant.contracts", "quant.validation", "quant.horizons",
    "quant.knowledge_v03", "quant.agents", "quant.evaluation_v02",
]


def _probe_script() -> str:
    return "\n".join([
        "import importlib, sys",
        "mods = " + repr(CORE_MODULES),
        "failed = []",
        "for m in mods:",
        "    try:",
        "        importlib.import_module(m)",
        "    except Exception as e:",
        "        failed.append((m, type(e).__name__))",
        "print('FAILED', failed)",
        "sys.exit(1 if failed else 0)",
    ])


def run_module_delete_test(modules: list[str] | None = None) -> dict[str, Any]:
    targets = modules or ["quant.validation", "quant.timeguard_reader",
                          "quant.knowledge_v03", "quant.horizons"]
    tmp = Path(tempfile.mkdtemp(prefix="v041_moddel_"))
    pkg = tmp / "quant"
    shutil.copytree(ROOT / "python/quant", pkg)
    probe = tmp / "probe.py"
    probe.write_text(_probe_script(), encoding="utf-8")
    results = {}
    for mod in targets:
        rel = Path(*mod.split(".")[1:]).with_suffix(".py")
        target = pkg / rel
        if not target.exists():
            results[mod] = {"deleted_exit": None, "restored_exit": None,
                            "ok": False, "reason": f"module file missing: {rel}"}
            continue
        saved = target.read_bytes()
        target.unlink()
        env = {**__import__("os").environ, "PYTHONPATH": str(tmp),
               "QUANT_ROOT": str(ROOT)}
        with_del = subprocess.run([sys.executable, str(probe)], capture_output=True,
                                  text=True, env=env, cwd=str(ROOT), timeout=60)
        target.write_bytes(saved)
        with_rest = subprocess.run([sys.executable, str(probe)], capture_output=True,
                                   text=True, env=env, cwd=str(ROOT), timeout=60)
        results[mod] = {
            "deleted_exit": with_del.returncode,
            "restored_exit": with_rest.returncode,
            "deleted_output": with_del.stdout.strip()[:200],
            "ok": with_del.returncode != 0 and with_rest.returncode == 0,
            "reason": ("删除后 FAIL、恢复后 PASS" if with_del.returncode != 0
                       and with_rest.returncode == 0 else
                       f"异常: 删除={with_del.returncode}, 恢复={with_rest.returncode}"),
        }
    shutil.rmtree(tmp, ignore_errors=True)
    ok = all(v["ok"] for v in results.values())
    return {"ok": ok, "modules": results,
            "note": "原始项目只读；独立进程真实导入；不使用 find_spec/文件路径判断"}
