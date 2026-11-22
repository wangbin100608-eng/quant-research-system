"""v0.3.0 综合测试报告：pytest + 覆盖率 + 验证 + 性能 + 安全 + 验收。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from quant.evaluation import PredictionStore
from quant.knowledge_v03 import KnowledgeSystemV03
from quant.utils import get_config


def _load(name, default=None):
    p = ROOT / "data/reports" / name
    return json.loads(p.read_text()) if p.exists() else (default or {})


def main():
    tr = subprocess.run([sys.executable, "-m", "pytest", str(ROOT / "python/tests"), "-q", "--tb=no"],
                        cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT / "python")},
                        capture_output=True, text=True)
    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "version": get_config()["project"]["version"],
        "pytest": {"exit_code": tr.returncode,
                   "summary": tr.stdout.strip().splitlines()[-1] if tr.stdout else ""},
        "coverage_v03": _load("coverage_report_v03.json"),
        "validation": _load("validation_report_v03.json"),
        "performance_v03": _load("performance_report_v03.json"),
        "final_acceptance": _load("final_acceptance_v03.json"),
        "prediction_integrity": PredictionStore().verify_all(),
    }
    ks = KnowledgeSystemV03()
    report["knowledge"] = {
        "libraries": sorted({r["library"] for r in ks.meta.query("SELECT DISTINCT library FROM kb_items")}),
        "hypotheses": len(ks.list_hypotheses()),
        "rejected_hypotheses": len(ks.list_hypotheses(
            __import__("quant.knowledge_v03", fromlist=["HypothesisStatus"]).HypothesisStatus.REJECTED)),
    }
    p = ROOT / "data/reports/test_report_v03.json"
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"pytest": report["pytest"]["summary"],
                      "integrity": report["prediction_integrity"]["ok"],
                      "knowledge": report["knowledge"], "report": str(p)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
