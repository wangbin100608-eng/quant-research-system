"""v0.2.0 阶段8 最终验收脚本：逐年核对预测→冻结→导入真实→误差→更新知识库→下一年。"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from quant.contracts import PredictionSpec, parse_date
from quant.evaluation import PredictionStore
from quant.knowledge import KnowledgeBase
from quant.storage import MetadataDB
from quant.timeguard import TimeTravelEngine
from quant.utils import ROOT, resolve


def main():
    meta = MetadataDB()
    store = PredictionStore()
    kb = KnowledgeBase()
    tg = TimeTravelEngine()
    out = {"generated_at": date.today().isoformat(), "data_origin": "real", "years": {}}

    # 逐年链路
    for y in (2002, 2003, 2004):
        ck_path = resolve(f"data/checkpoints/year_{y}.json")
        ck = json.loads(ck_path.read_text()) if ck_path.exists() else {}
        completed = ck.get("completed", [])
        # 预测记录
        preds = store.load(y)
        # 快照（当前预测用 asof_{y-1}）
        snap = ROOT / f"data/knowledge/snapshots/asof_{y-1}"
        spec = PredictionSpec.for_year(y, 2000, f"asof_{y-1}", "audit", "feature_v1")
        actual = resolve(f"data/knowledge/raw_documents/actuals/actual_{y}.parquet")
        out["years"][str(y)] = {
            "steps_completed": len(completed),
            "freeze_before_import": ("freeze_prediction" in completed and "import_actual" in completed
                                     and completed.index("freeze_prediction") < completed.index("import_actual")),
            "prediction_records": len(preds),
            "prediction_hash": preds[0]["hash"][:16] if preds else None,
            "snapshot_sealed": kb.is_sealed(y - 1),
            "actual_imported": actual.exists(),
            "metrics": ck.get("artifacts", {}).get("metrics"),
            "knowledge_updated": "update_knowledge" in completed,
        }

    # 最重要的验收：三年可访问空间无未来数据
    boundary = {}
    for y, yr in ((2002, 2001), (2003, 2002), (2004, 2003)):
        snap = ROOT / f"data/knowledge/snapshots/asof_{yr}"
        cutoff = date(yr, 12, 31)
        violations = []
        for ti in snap.rglob("*.timeindex"):
            for line in ti.read_text().splitlines():
                v = line.strip()
                if v and v.lower() not in ("unknown", "null", "none"):
                    if parse_date(v) > cutoff:
                        violations.append(v)
        # 全目录 hash 校验
        vr = kb.verify_snapshot(yr)
        boundary[f"predict_{y}"] = {
            "cutoff": cutoff.isoformat(),
            "future_rows_in_accessible_space": len(violations),
            "hash_consistent": vr["checks"].get("hashes_match"),
            "sealed": vr["checks"].get("sealed"),
            "PASS": len(violations) == 0 and vr["checks"].get("hashes_match", False),
        }
    out["time_isolation_proof"] = boundary
    out["prediction_integrity"] = store.verify_all()
    out["all_snapshots_sealed"] = all(kb.is_sealed(y) for y in (2001, 2002, 2003, 2004))
    out["PASS"] = (
        all(v["PASS"] for v in boundary.values())
        and out["prediction_integrity"]["ok"]
        and out["all_snapshots_sealed"]
        and all(v["steps_completed"] == 17 and v["freeze_before_import"]
                and v["prediction_records"] >= 1 for v in out["years"].values())
    )
    path = resolve("data/reports/final_acceptance.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"PASS": out["PASS"], "boundary": boundary,
                      "all_sealed": out["all_snapshots_sealed"],
                      "integrity": out["prediction_integrity"]["ok"],
                      "report": str(path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
