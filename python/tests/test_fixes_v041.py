"""v0.4.1 修复验收测试：P0 验收器 + P1 分批/SQL 门禁。"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.module_delete_v041 import run_module_delete_test
from quant.report_verifier_v041 import ReportVerifierV041, compute_report_sha
from quant.strict_kb_v041 import check_six_libraries
from quant.test_manifest_v041 import build_manifest, verify_execution
from quant.timeguard_reader import TimeGuardReader, TimeGuardReadError
from quant.timestamps_v04 import cutoff_for_year


def test_module_delete_real_subprocess():
    r = run_module_delete_test(["quant.validation", "quant.timeguard_reader"])
    assert r["ok"], r["modules"]
    for k, v in r["modules"].items():
        assert v["deleted_exit"] != 0 and v["restored_exit"] == 0


def test_report_verifier_detects_tampering(tmp_path):
    report = {"version": "v0.4.1", "generated_at": datetime.now(timezone.utc).isoformat(),
              "commit": "abc123", "pytest": {"summary": "94 passed"},
              "summary": {"passed": 1}, "checks": [{"result": "PASS"}]}
    report["report_sha256"] = compute_report_sha(report)
    p = tmp_path / "r.json"
    p.write_text(json.dumps(report))
    v = ReportVerifierV041(expected_commit="abc123", expected_counts={"passed": 1})
    ok, errs = v.verify(p)
    assert ok, errs
    # 篡改数量
    report["summary"]["passed"] = 999
    p.write_text(json.dumps(report))
    ok, errs = v.verify(p)
    assert not ok and any("passed" in e or "sha256" in e for e in errs)
    # 篡改 commit
    report["summary"]["passed"] = 1
    report["commit"] = "deadbeef"
    p.write_text(json.dumps(report))
    ok, errs = v.verify(p)
    assert not ok and any("commit" in e for e in errs)


def test_strict_kb_empty_and_missing(tmp_path, monkeypatch):
    from quant.knowledge_v03 import KnowledgeSystemV03
    from quant.storage import MetadataDB
    import quant.strict_kb_v041 as sk
    monkeypatch.setattr(KnowledgeSystemV03, "__init__",
                        lambda self, strict=None: setattr(self, "meta", MetadataDB(str(tmp_path / "kb.db"))) or None)
    kb = KnowledgeSystemV03()
    # 临时库需先建 kb_items 表（空库负向测试）
    kb.meta.conn.execute("CREATE TABLE IF NOT EXISTS kb_items (item_id TEXT, library TEXT, "
                         "available_time TEXT, published_time TEXT, source TEXT, version TEXT, "
                         "snapshot_id TEXT)")
    kb.meta.conn.execute("CREATE TABLE IF NOT EXISTS hypotheses (hypothesis_id TEXT, cutoff_date TEXT)")
    kb.meta.conn.commit()
    r = check_six_libraries(kb)
    assert not r["ok"]
    assert any("missing_libraries" in e for e in r["errors"])


def test_manifest_delete_and_tamper():
    m = build_manifest()
    assert m["count"] > 0 and m["test_ids"]
    assert verify_execution(m, f"{m['count']} passed, 0 warnings")["ok"]
    assert not verify_execution(m, "1 passed, 2 failed")["ok"]


def test_query_sql_time_gate(tmp_path):
    p = tmp_path / "q.parquet"
    pq.write_table(pa.table({"symbol": ["a", "b"],
                             "available_at": ["2001-06-01T00:00:00Z", "2003-01-01T00:00:00Z"],
                             "v": [1, 2]}), str(p))
    reader = TimeGuardReader()
    # 显式 cutoff + 结果含 available_at → 只返回 2001 行
    out = reader.query_sql(f"SELECT * FROM read_parquet('{p}')", cutoff_for_year(2002))
    assert len(out) == 1 and out[0]["symbol"] == "a"
    # 结果无 available_at → 严格拒绝
    with pytest.raises(TimeGuardReadError):
        reader.query_sql(f"SELECT symbol, v FROM read_parquet('{p}')", cutoff_for_year(2002))
    # 子查询绕过尝试：外层仍按结果列过滤
    out2 = reader.query_sql(f"SELECT * FROM (SELECT * FROM read_parquet('{p}')) t",
                            cutoff_for_year(2002))
    assert len(out2) == 1


def test_stream_matches_read_v041(tmp_path):
    days = ["2001-06-01T00:00:00Z"] * 500 + ["2003-01-01T00:00:00Z"] * 500
    p = tmp_path / "s.parquet"
    pq.write_table(pa.table({"symbol": [f"S{i}" for i in range(1000)],
                             "available_at": days, "v": list(range(1000))}), str(p))
    reader = TimeGuardReader()
    full, _ = reader.read_parquet(p, cutoff_for_year(2002))
    streamed = [b for b, _ in reader.stream_parquet(p, cutoff_for_year(2002), batch_size=64)]
    assert sum(b.num_rows for b in streamed) == full.num_rows == 500


def test_no_fabricated_ingested_at():
    """v0.4.1：缺少 ingested_at 时不得用当前时间冒充历史采集时间。"""
    from quant.timestamps_v04 import migrate_record, time_precision
    r = migrate_record({"available_time": "2001-06-01", "symbol": "X"})
    assert r["ingested_at"] == "unknown"
    assert r["field_provenance"]["ingested_at"] == "unknown_no_historical_record"
    assert r["migration_log"]["policy"] == "unknown_ingested_at_never_fabricated"
    assert r["time_trust"] == "low"
    assert time_precision("2001-06-01") == "date_only"
    assert time_precision("2001-06-01T08:00:00+08:00") == "full_timestamp"


def test_date_only_cutoff_day_strict_reject():
    """截止日当天仅有日期的数据，严格模式必须保守拒绝。"""
    from quant.timestamps_v04 import available_ok, cutoff_for_year
    cut = cutoff_for_year(2002)  # 2001-12-31T23:59:59Z
    assert available_ok("2001-12-31", cut, strict=False) is True
    assert available_ok("2001-12-31", cut, strict=True) is False
    assert available_ok("2001-12-30", cut, strict=True) is True
    assert available_ok("2001-12-31T20:00:00Z", cut, strict=True) is True
