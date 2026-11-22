"""v0.3.0 P1-1/P1-3：六库、假设状态机、失败假设保留、严格隔离测试。"""
from __future__ import annotations

from datetime import date
import pytest

from quant.knowledge_v03 import KnowledgeSystemV03, HypothesisStatus as HS
from quant.timeguard import LeakError


@pytest.fixture
def ks():
    return KnowledgeSystemV03()


def test_six_libraries_writes_and_queries(ks):
    snap = "asof_2001"
    cut = date(2001, 12, 31)
    ks.add_structured("s1", snap, "real:test", "2001-11-01", {"pe": 20})
    ks.add_document("d1", snap, "real:news", "2001-10-01", "2001-10-02", "新闻正文")
    ks.add_vector("v1", snap, "real:news", "2001-10-02", "新闻", [0.1]*8)
    ks.add_event("e1", snap, "real:sec", "2001-09-30", "2001-10-15", "2001-10-15", "财报发布")
    ks.add_rule("r1", snap, "动量规则", status="pending")
    ks.create_hypothesis("h1", "动量有效", cut, {"falsification": "IC<0"}, "linear", snap)
    for lib in ("structured", "document", "vector", "event", "rule"):
        rows = ks.query(lib, cut, snap)
        assert rows, lib
    assert len(ks.list_hypotheses()) >= 1


def test_future_records_not_returned(ks):
    snap = "asof_2001"
    ks.add_document("future_doc", snap, "real:news", "2002-05-01", "2002-05-01", "未来新闻")
    rows = ks.query("document", date(2001, 12, 31), snap)
    assert all(r["item_id"] != "future_doc" for r in rows)


def test_snapshot_isolation(ks):
    cut = date(2001, 12, 31)
    ks.add_document("snapA", "asof_2001", "real:x", "2001-05-01", "2001-05-01", "A")
    ks.add_document("snapB", "asof_2002", "real:x", "2001-05-01", "2001-05-01", "B")
    rows = ks.query("document", cut, "asof_2001")
    assert "snapA" in [r["item_id"] for r in rows]
    assert "snapB" not in [r["item_id"] for r in rows]


def test_strict_rejects_unknown_available(ks):
    with pytest.raises(LeakError):
        ks.add_document("bad", "asof_2001", "real:x", None, None, "无时间")


def test_hypothesis_lifecycle_keeps_failed(ks):
    cut = date(2001, 12, 31)
    ks.create_hypothesis("h_fail", "价格反转", cut,
                         {"falsification": "方向准确率<0.5", "method": "walk_forward"},
                         "lightgbm", "asof_2001")
    ks.update_hypothesis("h_fail", HS.REJECTED,
                         validation_result={"direction_accuracy": 0.42, "n": 100})
    ks.create_hypothesis("h_ok", "动量延续", cut,
                         {"falsification": "IC<0", "method": "purged"},
                         "linear", "asof_2001")
    ks.update_hypothesis("h_ok", HS.VALIDATED,
                         validation_result={"rank_ic": 0.08, "n": 200})
    rejected = ks.list_hypotheses(HS.REJECTED)
    validated = ks.list_hypotheses(HS.VALIDATED)
    assert any(h["hypothesis_id"] == "h_fail" for h in rejected)  # 失败假设保留
    assert any(h["hypothesis_id"] == "h_ok" for h in validated)
    assert len(json_hist(ks, "h_fail")) >= 2  # 历史轨迹保留


def test_validated_without_oos_rejected(ks):
    ks.create_hypothesis("h_tmp", "测试", date(2001, 12, 31), {}, "linear", "asof_2001")
    with pytest.raises(ValueError):
        ks.update_hypothesis("h_tmp", HS.VALIDATED)  # 无样本外结果
    with pytest.raises(ValueError):
        ks.add_rule("bad_rule", "asof_2001", "未验证规则", status="validated")


def json_hist(ks, hid):
    import json
    r = ks.meta.query("SELECT history FROM hypotheses WHERE hypothesis_id=?", (hid,))[0]
    return json.loads(r["history"])
