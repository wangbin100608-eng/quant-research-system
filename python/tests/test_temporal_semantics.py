"""数据真实发布时间语义测试：按 available_time 而非 event_time 判断可用性。"""
from __future__ import annotations

import json

import pyarrow.parquet as pq

from quant.contracts import PredictionSpec
from quant.timeguard import LeakError, TimeTravelEngine
from quant.utils import resolve


def test_2001_annual_report_published_2002_not_available_for_2002_prediction():
    """黄金样例：2001 年度报告在 2002-03-15 才披露，预测 2002 时必须拒绝。"""
    t = pq.read_table("data/raw/raw_all.parquet", columns=[
        "symbol", "domain", "event_time", "published_time", "available_time", "payload"])
    rows = t.to_pylist()
    # 找到 fixture 中 event=2001 且 Q4 的财报（期末 2001-12-28，披露 2002-03-15）
    matches = [r for r in rows if r["domain"] == "fundamental"
               and str(r["event_time"]).startswith("2001")
               and str(r["available_time"]).startswith("2002-03-15")]
    assert len(matches) > 0, "fixture 应包含 2001 年报在 2002-03-15 披露的样例"
    tg = TimeTravelEngine()
    spec = PredictionSpec.for_year(2002, 2000, "asof_2001", "test", "feature_v1")
    for r in matches[:3]:
        try:
            tg.validate_row(r, spec)
        except LeakError:
            pass
        else:
            raise AssertionError(f"未来财报未被拒绝: {r}")


def test_news_uses_publish_time_not_event_time():
    """新闻按发布时间准入（本 fixture 中 published == available）。"""
    t = pq.read_table("data/raw/raw_all.parquet", columns=[
        "domain", "event_time", "published_time", "available_time"])
    news = [r for r in t.to_pylist() if r["domain"] == "news"]
    assert news
    assert all(r["published_time"] == r["available_time"] for r in news)


def test_index_members_are_historical_versions():
    """指数成分使用历史版本而非今天名单，并保留退市股。"""
    t = pq.read_table("data/raw/raw_all.parquet", columns=["domain", "payload", "available_time"])
    members = [json.loads(r["payload"]) for r in t.to_pylist() if r["domain"] == "index_member"]
    delisted = {m["member"] for m in members if m.get("delisted")}
    assert "600001.SH" in delisted and "ENRON" in delisted
    years = {json.loads(r["payload"])["effective_year"]
             for r in t.to_pylist() if r["domain"] == "index_member"}
    assert years == {2000, 2001, 2002, 2003, 2004}
