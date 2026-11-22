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
        """P0 point-in-time 股票池：每年 cutoff=Y-1-12-31，仅用此前真实可得记录。

        * 退市记录：同时检查记录 available_time（信息发布时间）与真实上市/退市日期；
          未来才公布的退市信息不得反向修正早期快照。
        * 指数成分/行业：当前名单只作参考并标 BLOCKED，不冒充历史版本。
        """
        from quant.timestamps_v04 import cutoff_for_year
        snapshots = {}
        for y in range(start_year, end_year + 1):
            cutoff = cutoff_for_year(y)  # Y 年初股票池截止 = Y-1 年末 23:59:59Z
            members: dict[str, dict] = {}
            source_stats: dict[str, int] = {}
            for r in self.rows:
                av = r.get("available_time")
                if not av:
                    continue  # unknown 不进严格快照
                try:
                    av_dt = datetime.fromisoformat(str(av).replace("Z", "+00:00"))
                    if av_dt.tzinfo is None:
                        av_dt = av_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if av_dt > cutoff:
                    continue  # 未来才可获得的信息，禁止进入早期快照
                pl = r["payload"]
                if r["domain"] == "delisted":
                    ld = str(pl.get("list_date", ""))[:4]
                    dd = str(pl.get("delist_date", ""))[:4]
                    if ld.isdigit() and int(ld) <= y and (not dd.isdigit() or int(dd) >= y):
                        members[r["symbol"]] = {"status": "listed",
                                                "basis": "real_delisting_record",
                                                "available_time": av,
                                                "delist_date": pl.get("delist_date")}
                    elif dd.isdigit() and int(dd) < y:
                        members.setdefault(r["symbol"], {
                            "status": "delisted", "basis": "real_delisting_record",
                            "available_time": av, "delist_date": pl.get("delist_date")})
                    source_stats["delisted"] = source_stats.get("delisted", 0) + 1
                elif r["domain"] == "index_member":
                    # 当前名单仅作参考，历史成分不可验证 → BLOCKED
                    members.setdefault(r["symbol"], {
                        "status": "member_reference", "basis": "current_members",
                        "available_time": av, "status_note": "BLOCKED: no historical member version"})
                    source_stats["index_member_reference"] = source_stats.get(
                        "index_member_reference", 0) + 1
                elif r["domain"] == "industry":
                    members.setdefault(r["symbol"], {
                        "status": "industry_reference", "basis": "current_classification",
                        "available_time": av, "status_note": "BLOCKED: no historical industry version"})
                    source_stats["industry_reference"] = source_stats.get("industry_reference", 0) + 1
            snapshots[y] = {
                "year": y, "cutoff": cutoff.isoformat(), "symbols": len(members),
                "delisted_count": sum(1 for v in members.values() if v["status"] == "delisted"),
                "current_member_references": sum(
                    1 for v in members.values() if v["basis"] == "current_members"),
                "source_stats": source_stats,
                "members": members,
                "time_audit": {"cutoff": cutoff.isoformat(),
                               "records_considered": len(self.rows),
                               "records_admitted": sum(source_stats.values())},
                "status": "BLOCKED" if source_stats.get("index_member_reference") else "PARTIAL",
                "note": ("PIT cutoff applied; 退市股双时间校验; 指数成分/行业仅为当前版本参考"
                         "（BLOCKED，不冒充历史版本）"),
            }
        return snapshots


class DataQuality:
    def __init__(self, raw_path: str | Path = "data/raw/raw_v03.parquet"):
        self.raw = resolve(str(raw_path))
        self.checks: list[dict[str, Any]] = []

    def run(self, batch_size: int = 65536) -> dict[str, Any]:
        """v0.6 P3/P4：Arrow RecordBatch 分批处理（不全表读取/不构建完整 keys 列表）。"""
        import math
        pf = pq.ParquetFile(str(self.raw))
        cols = [c for c in ("symbol", "domain", "available_time", "payload", "source")
                if c in pf.schema_arrow.names]
        miss_time = miss_source = dup = bad_price = bad_volume = 0
        nan_count = inf_count = ohlc_bad = out_of_order = 0
        seen: set[tuple] = set()
        last_time: dict[str, str] = {}
        rows = 0
        for batch in pf.iter_batches(batch_size=batch_size, columns=cols):
            d = {c: batch.column(c).to_pylist() for c in cols}
            n = batch.num_rows
            rows += n
            for i in range(n):
                av = d.get("available_time", [None] * n)[i]
                sym = d.get("symbol", [""] * n)[i]
                dom = d.get("domain", [""] * n)[i]
                src = d.get("source", [""] * n)[i]
                pay = d.get("payload", [""] * n)[i]
                if not av:
                    miss_time += 1
                if not src:
                    miss_source += 1
                key = (sym, dom, av, pay)
                if key in seen:
                    dup += 1
                else:
                    seen.add(key)
                if av and dom:
                    grp = f"{dom}|{sym}"
                    if grp in last_time and av < last_time[grp]:
                        out_of_order += 1
                    last_time[grp] = av
                try:
                    obj = json.loads(pay or "{}")
                except Exception:
                    continue
                c = obj.get("close")
                v = obj.get("volume")
                for x in (c, v, obj.get("open"), obj.get("high"), obj.get("low")):
                    if isinstance(x, float) and math.isnan(x):
                        nan_count += 1
                    elif isinstance(x, float) and math.isinf(x):
                        inf_count += 1
                if c is not None and (not isinstance(c, (int, float)) or c <= 0):
                    bad_price += 1
                if v is not None and (not isinstance(v, (int, float)) or v < 0):
                    bad_volume += 1
                hi, lo, op, cl = (obj.get("high"), obj.get("low"),
                                  obj.get("open"), obj.get("close"))
                if None not in (hi, lo, op, cl) and (hi < lo or not (lo <= op <= hi) or not (lo <= cl <= hi)):
                    ohlc_bad += 1
        checks = [
            {"name": "missing_time", "count": miss_time, "pass": miss_time == 0},
            {"name": "duplicate_rows", "count": dup, "pass": dup == 0},
            {"name": "abnormal_price", "count": bad_price, "pass": bad_price == 0},
            {"name": "abnormal_volume", "count": bad_volume, "pass": bad_volume == 0},
            {"name": "missing_source", "count": miss_source, "pass": miss_source == 0},
            {"name": "time_order", "count": out_of_order, "pass": out_of_order == 0},
            {"name": "nan_values", "count": nan_count, "pass": nan_count == 0},
            {"name": "infinity_values", "count": inf_count, "pass": inf_count == 0},
            {"name": "ohlc_consistency", "count": ohlc_bad, "pass": ohlc_bad == 0},
        ]
        dims = {
            "completeness": sum(1 for c in checks if c["name"] in ("missing_time", "missing_source") and c["pass"]) / 2 * 100,
            "accuracy": sum(1 for c in checks if c["name"] in ("abnormal_price", "abnormal_volume", "nan_values", "infinity_values") and c["pass"]) / 4 * 100,
            "consistency": sum(1 for c in checks if c["name"] in ("duplicate_rows", "time_order", "ohlc_consistency") and c["pass"]) / 3 * 100,
        }
        score = round(sum(1 for c in checks if c["pass"]) / len(checks) * 100, 1)
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
                "rows": rows, "checks": checks, "quality_score": score,
                "dimensions": dims, "batch_size": batch_size,
                "note": "P3/P4: Arrow分批; 维度评分不掩盖关键字段缺失; 评分不代替逐项检查"}
