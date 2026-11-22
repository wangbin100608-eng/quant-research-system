"""合并 v0.2 A股指数真实日线（raw_all）到 v0.3 数据集（raw_v03），按五元组去重。

背景：v0.3 抓取时东财 kline 接口被限流，但 v0.2 已成功抓取上证/深证 2000-2004 真实日线。
这些是真实数据（source=fixture? 不 —— v0.2 raw_all 是 fixture！）。
注意：raw_all.parquet 是 **fixture 合成数据**，不能合并进真实数据集！
因此本脚本仅用于在 raw_all 中来源为 real 时合并；否则拒绝并明确报告 A股日线缺口。
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from quant.utils import resolve
from quant.logging_util import log


def main():
    v03 = resolve("data/raw/raw_v03.parquet")
    old = resolve("data/raw/raw_all.parquet")
    t3 = pq.read_table(str(v03))
    if not old.exists():
        print("NO_OLD_RAW"); return
    told = pq.read_table(str(old), columns=["source", "domain", "symbol", "available_time", "payload"])
    srcs = set(told.column("source").to_pylist())
    real_a_price = [r for r in zip(told.column("source").to_pylist(),
                                   told.column("domain").to_pylist(),
                                   told.column("symbol").to_pylist(),
                                   told.column("available_time").to_pylist(),
                                   told.column("payload").to_pylist())
                    if r[1] == "price" and not str(r[0]).startswith("fixture")]
    if not real_a_price:
        report = {
            "merge": "skipped",
            "reason": "raw_all.parquet 中 A股日线全部为 fixture 合成数据；按约束不得合并进真实数据集",
            "a_share_daily_gap": "东财 kline 接口限流，v0.3 未新增 A股日线；v0.2 的 A股日线为合成，不能充作真实数据",
            "result": "如实记录缺口，不用合成数据补齐",
        }
        p = resolve("data/reports/a_share_price_gap.json")
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    # 若未来抓到真实 A股日线，走这里合并（保留接口）
    print(f"MERGE_READY real_a_price_rows={len(real_a_price)}")


if __name__ == "__main__":
    main()
