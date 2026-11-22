"""v0.5.0 P3：历史股票池快照 + 数据质量评分。

股票池：从 raw_v03 的 delisted/info/index_member/industry 构建 annual_universe；
保留退市股（防幸存者偏差），标注 basis（current_members / real_delisting_record），
不以当前上市股反向构造历史池。

数据质量：对真实数据输出 缺失值/重复/异常价格/异常成交量/时间顺序/来源完整性/时间完整性 检查，
生成评分（评分不能代替原始检查结果）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from quant.utils import resolve


class HistoricalUniverse:
    def __init__(self, raw_path: str | Path = "data/raw/raw_v03.parquet"):
        self.raw = resolve(str(raw_path))
        self.rows: list[dict[str, Any]] = []
        if self.raw.exists():
            t = pq.read_table(str(self.raw), columns=[
                "symbol", "market", "domain", "available_time", "payload", "source"])
            for s, m, d, av, p, src in zip(
                    t.column("symbol").to_pylist(), t.column("market").to_pylist(),
                    t.column("domain").to_pylist(), t.column("available_time").to_pylist(),
                    t.column("payload").to_pylist(), t.column("source").to_pylist()):
                if d not in ("delisted", "info", "index_member", "industry"):
                    continue
                try:
                    payload = json.loads(p or "{}")
                except Exception:
                    continue
                self.rows.append({"symbol": s, "market": m, "domain": d,
                                  "available_time": av, "source": src, "payload": payload})

    def build_snapshots(self, start_year: int = 2000, end_year: int = 2004) -> dict[int, dict]:
        snapshots = {}
        for y in range(start_year, end_year + 1):
            members: dict[str, dict] = {}
            for r in self.rows:
                pl = r["payload"]
                # 退市股：按真实上市/退市日期判断当年是否在池
                if r["domain"] == "delisted":
                    ld = str(pl.get("list_date", ""))[:4]
                    dd = str(pl.get("delist_date", ""))[:4]
                    if ld.isdigit() and int(ld) <= y and (not dd.isdigit() or int(dd) >= y):
                        members[r["symbol"]] = {"status": "listed", "basis": "real_delisting_record",
                                                "delist_date": pl.get("delist_date")}
                    elif dd.isdigit() and int(dd) < y:
                        members.setdefault(r["symbol"], {"status": "delisted",
                                                         "basis": "real_delisting_record",
                                                         "delist_date": pl.get("delist_date")})
                # 指数成分（当前名单，标注 current_members；仅作参考，不冒充历史）
                elif r["domain"] == "index_member":
                    members.setdefault(r["symbol"], {"status": "member_reference",
                                                     "basis": "current_members"})
                # 行业（当前分类）
                elif r["domain"] == "industry":
                    members.setdefault(r["symbol"], {"status": "industry_reference",
                                                     "basis": "current_classification"})
            snapshots[y] = {
                "year": y, "symbols": len(members),
                "delisted_count": sum(1 for v in members.values() if v["status"] == "delisted"),
                "current_member_references": sum(1 for v in members.values()
                                                 if v["basis"] == "current_members"),
                "members": members,
                "note": "退市股使用真实上市/退市日期；指数成分与行业仅为当前版本参考，不替代历史版本",
            }
        return snapshots


class DataQuality:
    def __init__(self, raw_path: str | Path = "data/raw/raw_v03.parquet"):
        self.raw = resolve(str(raw_path))
        self.checks: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        t = pq.read_table(str(self.raw))
        rows = t.num_rows
        syms = t.column("symbol").to_pylist()
        doms = t.column("domain").to_pylist()
        times = t.column("available_time").to_pylist()
        pays = t.column("payload").to_pylist()
        srcs = t.column("source").to_pylist()
        # 缺失时间
        miss_time = sum(1 for v in times if not v)
        # 重复（symbol+domain+available_time+payload）
        keys = list(zip(syms, doms, times, pays))
        dup = rows - len(set(keys))
        # 异常价格/成交量
        bad_price = bad_volume = 0
        for p in pays:
            try:
                d = json.loads(p or "{}")
            except Exception:
                continue
            c = d.get("close")
            v = d.get("volume")
            if c is not None and (not isinstance(c, (int, float)) or c <= 0):
                bad_price += 1
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                bad_volume += 1
        # 来源完整性
        miss_source = sum(1 for s in srcs if not s)
        # 时间顺序（同 domain 的时间是否单调，按出现顺序简单检查）
        out_of_order = 0
        last = {}
        for d, tm in zip(doms, times):
            if not tm:
                continue
            if d in last and tm < last[d]:
                out_of_order += 1
            last[d] = tm
        checks = [
            {"name": "missing_time", "count": miss_time, "pass": miss_time == 0},
            {"name": "duplicate_rows", "count": dup, "pass": dup == 0},
            {"name": "abnormal_price", "count": bad_price, "pass": bad_price == 0},
            {"name": "abnormal_volume", "count": bad_volume, "pass": bad_volume == 0},
            {"name": "missing_source", "count": miss_source, "pass": miss_source == 0},
            {"name": "time_order", "count": out_of_order, "pass": out_of_order == 0},
        ]
        score = round(sum(1 for c in checks if c["pass"]) / len(checks) * 100, 1)
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "rows": rows, "checks": checks, "quality_score": score,
                "note": "评分不代替逐项检查结果；指数成分/行业当前版本不作为历史版本验证依据"}
