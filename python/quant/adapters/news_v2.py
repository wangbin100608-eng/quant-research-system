"""新闻/公告适配器（v0.2.0）。

公开新闻 API 对 2000-2004 历史覆盖普遍不完整。本适配器：
  * 尝试 GDELT DOC API（若当时段有数据则抓取，published_time=真实发布时间）；
  * 无论成功与否，明确记录 2000-2004 历史新闻的覆盖缺口，绝不用合成数据补齐。
SEC 的 8-K 公告由 sec_v2 适配器提供（真实披露日）。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .real_base import RealAdapterBase
from ..logging_util import log

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


class NewsAdapterV2(RealAdapterBase):
    name = "news_v2"

    def fetch(self) -> FetchResult:
        rows: list[dict[str, Any]] = []
        missing = []
        for year in range(2000, 2005):
            try:
                params = {
                    "query": "(stock market OR earnings) sourcelang:english",
                    "mode": "artlist", "maxrecords": 50, "format": "json",
                    "startdatetime": f"{year}0101000000",
                    "enddatetime": f"{year}1231235959",
                }
                url = GDELT + "?" + "&".join(f"{k}={v}" for k, v in params.items())
                data = self.get(url).json()
                arts = data.get("articles", []) or []
                for a in arts:
                    s = a.get("seendate", "")  # yyyyMMddHHmmss
                    if len(s) < 8:
                        continue
                    try:
                        d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                    except ValueError:
                        continue
                    rows.append(self.real_row(
                        "MARKET", "GLOBAL", "news", "gdelt", d, d, d,
                        {"title": a.get("title", ""), "url": a.get("url", ""),
                         "domain": a.get("domain", "")},
                    ))
                if not arts:
                    missing.append({"year": year, "reason": "GDELT 该年无返回（历史覆盖缺口）"})
            except Exception as e:
                missing.append({"year": year, "reason": str(e)})
                log.warning("GDELT %s 失败: %s", year, e)
        # 明确总体缺口
        missing.append({"domain": "news",
                        "reason": "公开新闻源对 2000-2004 历史覆盖不完整；缺口如上逐年列出"})
        return self.coverage(rows, extra={"note": "新闻真实披露时间=seendate；历史覆盖可能为零"},
                             missing=missing)
