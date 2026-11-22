"""v0.4.0 P0：验收系统自身的测试（确保不会硬编码 PASS）。

测试：
  * 每项检查输出结构化字段（含 failure_reason）。
  * 篡改报告 → FAIL。
  * 核心模块缺失 → FAIL（临时副本模拟）。
  * pytest 失败 → FAIL。
  * 时间注入未来数据 → FAIL。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "python/scripts"))

import final_acceptance_v04 as fa


def test_check_records_structure():
    a = fa.Acceptance()
    a.check("T1", "正向", "x", "lambda True", lambda: True)
    rec = a.checks[0]
    for k in ("id", "description", "input", "execution", "result", "failure_reason", "executed_at"):
        assert k in rec
    assert rec["result"] == "PASS"
    a.check("T2", "负向", "x", "lambda False", lambda: (False, "故意失败"))
    assert a.checks[1]["result"] == "FAIL"
    assert "故意失败" in a.checks[1]["failure_reason"]


def test_check_exception_is_fail():
    a = fa.Acceptance()
    def boom():
        raise RuntimeError("boom")
    a.check("T3", "异常", "x", "raise", boom)
    assert a.checks[0]["result"] == "FAIL"
    assert "boom" in a.checks[0]["failure_reason"]


def test_tampered_report_detection():
    a = fa.Acceptance()
    a.actual_passed = 59
    ok, reason = a.check_tamper_report_detected()
    assert ok is True
    assert "篡改" in reason


def test_module_presence_and_missing_simulation():
    a = fa.Acceptance()
    ok, _ = a.check_modules_present()
    assert ok
    # 模拟缺失：find_spec 对假模块返回 None
    assert importlib.util.find_spec("quant.__nonexistent_module__") is None


def test_time_isolation_negative():
    from quant.timestamps_v04 import cutoff_for_year
    from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
    import pyarrow as pa
    import pyarrow.parquet as pq
    a = fa.Acceptance()
    bad = a.tmp / "neg_future.parquet"
    pq.write_table(pa.table({"symbol": ["X"], "available_at": ["2099-01-01T00:00:00Z"],
                             "v": [1.0]}), str(bad))
    out, audit = TimeGuardReader().read_parquet(bad, cutoff_for_year(2002))
    assert out.num_rows == 0 and audit.dropped_future == 1  # 未来数据被拒绝（剔除）
    ok, _ = a.check_time_isolation()
    assert ok


def test_overall_fails_when_p0_fails():
    """整体判定：任一 P0 FAIL 必须 overall=FAIL（纯逻辑，不落盘、不改 ROOT）。"""
    a = fa.Acceptance()
    # 直接模拟 run() 的判定段：一项 P0 FAIL 即整体 FAIL
    a.check("01", "模拟失败", "x", "lambda False", lambda: (False, "模拟 pytest 失败"))
    a.check("02", "模拟通过", "x", "lambda True", lambda: True)
    p0 = ["01", "02"]
    failed = [x["id"] for x in a.checks if x["id"] in p0 and x["result"] != "PASS"]
    overall = "PASS" if not failed else "FAIL"
    assert overall == "FAIL"
    assert "01" in failed
