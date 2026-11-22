"""v0.4 完善：抓取腾讯指数真实日线 → data/raw/raw_v04_indices.parquet + 覆盖率报告。

不修改 raw_v03.parquet 或任何历史报告；输出为独立新文件。
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from quant.adapters.tencent_index import TencentIndexAdapter
from quant.adapters.base import CANONICAL_SCHEMA
from quant.utils import resolve, ROOT


def main():
    res = TencentIndexAdapter().fetch()
    out = resolve("data/raw/raw_v04_indices.parquet")
    pq.write_table(res.table.cast(CANONICAL_SCHEMA), str(out), compression="zstd")
    syms = res.table.column("symbol").to_pylist()
    times = res.table.column("available_time").to_pylist()
    srcs = res.table.column("source").to_pylist()
    pays = res.table.column("payload").to_pylist()
    by_year = Counter(str(t)[:4] for t in times)
    by_sym = Counter(syms)
    hashes = set()
    for p in pays:
        try:
            h = json.loads(p).get("raw_sha256")
            if h:
                hashes.add(h)
        except Exception:
            pass
    import hashlib as _hl
    fh = _hl.sha256()
    with open(out, "rb") as _f:
        for _chunk in iter(lambda: _f.read(1 << 16), b""):
            fh.update(_chunk)
    file_sha = fh.hexdigest()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": "v0.4.0-improvement", "source": "tencent_fqkline",
        "synthetic": False, "data_origin": "real",
        "rows": res.table.num_rows,
        "symbols": dict(by_sym), "years": dict(sorted(by_year.items())),
        "source_prefixes": sorted(set(srcs)),
        "raw_sha256_count": len(hashes),
        "missing_ranges": res.missing_ranges,
        "file": str(out.relative_to(ROOT)),
        "file_sha256": file_sha,
    }
    rp = resolve("data/reports/coverage_report_v04_indices.json")
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rows": report["rows"], "symbols": report["symbols"],
                      "years": report["years"], "missing": report["missing_ranges"],
                      "report": str(rp.relative_to(ROOT))}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
