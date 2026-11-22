"""主 CLI：run / verify / report。"""
from __future__ import annotations

import argparse
import json

from quant.logging_util import log
from quant.orchestrator import AnnualLoop
from quant.evaluation import PredictionStore
from quant.utils import ROOT, get_config, resolve


def main():
    p = argparse.ArgumentParser(prog="quant")
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--start", type=int)
    r.add_argument("--end", type=int)
    r.add_argument("--raw", default=None,
                   help="原始 Parquet 路径；默认 data/raw/raw_all.parquet，可指定 raw_real.parquet")
    v = sub.add_parser("verify")
    a = p.parse_args()

    cfg = get_config()
    raw = resolve(a.raw) if getattr(a, "raw", None) else resolve(
        cfg["storage"]["raw_dir"] + "/raw_all.parquet")
    if a.cmd == "run":
        loop = AnnualLoop(raw)
        out = loop.run_all(a.start, a.end)
        print(json.dumps({
            "years": [
                {"year": y["year"], "metrics": y.get("metrics"),
                 "perf": y.get("perf"), "steps": len(y["completed_steps"])}
                for y in out["years"]
            ],
            "integrity": out["prediction_integrity"],
            "seals": out["seals"],
            "all_snapshots_sealed": out.get("all_snapshots_sealed", False),
        }, ensure_ascii=False, indent=2))
    elif a.cmd == "verify":
        store = PredictionStore()
        print(json.dumps(store.verify_all(), ensure_ascii=False, indent=2))
    else:
        p.print_help()


if __name__ == "__main__":
    main()
