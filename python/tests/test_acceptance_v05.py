"""v0.5.0 P0：验收系统自身的测试（负向闭环、非零退出码、结构化日志）。"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python/scripts"))

import final_acceptance_v05 as fa


def test_check_structure_contains_timing():
    a = fa.Acceptance05()
    a.check("T", False, "x", {}, "lambda True", lambda: True)
    c = a.checks[0]
    for k in ("id", "p0", "input", "execution", "result", "failure_reason",
              "started_at", "finished_at", "duration_seconds"):
        assert k in c
    assert c["result"] == "PASS" and c["duration_seconds"] >= 0


def test_check_exception_becomes_fail():
    a = fa.Acceptance05()
    a.check("E", True, "x", {}, "raise", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert a.checks[0]["result"] == "FAIL"
    assert "boom" in a.checks[0]["failure_reason"]


def test_module_delete_cycle_real():
    a = fa.Acceptance05()
    ok, reason = a.module_delete_cycle()
    assert ok, reason
    assert "删除后导入失败=True" in reason and "恢复后导入成功=True" in reason


def test_report_tamper_four_kinds():
    a = fa.Acceptance05()
    a.actual_passed = 73
    ok, reason = a.report_tamper_matrix()
    assert ok, reason
    for kind in ("passed", "total", "timestamp", "commit"):
        assert kind in reason


def test_injection_all_interfaces():
    a = fa.Acceptance05()
    ok, reason = a.injection_all_interfaces()
    assert ok, reason
    assert "parquet" in reason and "duckdb" in reason and "kb" in reason


def test_p0_fail_overall_fail():
    a = fa.Acceptance05()
    a.check("01", True, "x", {}, "false", lambda: (False, "故意失败"))
    failed = [c["id"] for c in a.checks if c["p0"] and c["result"] != "PASS"]
    assert failed == ["01"]


def test_exit_code_nonzero_on_fail(tmp_path):
    """子进程模拟一项 P0 FAIL 时，脚本以非零码退出。"""
    script = tmp_path / "fake.py"
    script.write_text(
        "import sys\n"
        "sys.path.insert(0, r'" + str(ROOT / "python") + "')\n"
        "sys.path.insert(0, r'" + str(ROOT / "python/scripts") + "')\n"
        "import final_acceptance_v05 as fa\n"
        "a=fa.Acceptance05()\n"
        "a.check('01',True,'x',{},'false',lambda:(False,'sim'))\n"
        "failed=[c['id'] for c in a.checks if c['p0'] and c['result']!='PASS']\n"
        "sys.exit(0 if not failed else 1)\n"
    )
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert r.returncode == 1
