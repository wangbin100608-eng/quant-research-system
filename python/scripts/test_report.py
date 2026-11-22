#!/usr/bin/env python3
"""v0.2.0 综合测试报告生成器：汇总防泄漏测试、覆盖率、快照审计、预测完整性、性能、验收。"""
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
from quant.storage import MetadataDB
from quant.timeguard import TimeTravelEngine
from quant.contracts import PredictionSpec
from quant.knowledge import KnowledgeBase
from quant.utils import resolve, get_config


def main():
    report = {"generated_at": datetime.now().isoformat(),
              "version": get_config()["project"]["version"]}

    # 1. pytest（全部测试）
    pr = subprocess.run(
        [sys.executable, "-m", "pytest", str(ROOT / "python/tests"), "-q", "--tb=no"],
        cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT / "python")},
        capture_output=True, text=True)
    report["pytest"] = {"exit_code": pr.returncode,
                        "summary": pr.stdout.strip().splitlines()[-1] if pr.stdout else ""}

    # 2. 覆盖率（真实 + fixture）
    cp = ROOT / "data/reports/coverage_report_real.json"
    report["coverage_real"] = json.loads(cp.read_text()) if cp.exists() else {"ok": False}
    fp = ROOT / "data/reports/coverage_report.json"
    report["coverage_fixture"] = json.loads(fp.read_text()) if fp.exists() else {}

    # 3. 时间隔离审计（v0.2.0：四年全覆盖 + KB verify）
    tg = TimeTravelEngine()
    kb = KnowledgeBase()
    audits = {}
    for y in (2001, 2002, 2003, 2004):
        spec = PredictionSpec.for_year(y + 1, 2000, f"asof_{y}", "report", "feature_v1")
        entry = {}
        try:
            entry["rust"] = tg.audit_snapshot(ROOT / f"data/knowledge/snapshots/asof_{y}", spec)
        except Exception as e:
            entry["rust"] = {"ok": False, "error": str(e)}
        entry["kb_verify"] = kb.verify_snapshot(y, spec, require_sealed=True)
        audits[f"asof_{y}"] = entry
    report["time_audits"] = audits

    # 4. 预测完整性
    report["prediction_integrity"] = PredictionStore().verify_all()

    # 5. 性能
    meta = MetadataDB()
    report["performance"] = meta.query(
        "SELECT task, elapsed_seconds, mem_peak_mb, rss_mb, rss_delta_mb, disk_bytes FROM metrics")

    # 6. 模型对比 / 性能报告 / 最终验收
    for fname, key in (("model_comparison.json", "model_comparison"),
                       ("performance_report.json", "performance_report"),
                       ("final_acceptance.json", "final_acceptance")):
        p = ROOT / "data/reports" / fname
        report[key] = json.loads(p.read_text()) if p.exists() else {}

    # 7. 快照冻结状态
    report["seals"] = {str(s["asof_year"]): bool(s["sealed"])
                       for s in meta.query("SELECT asof_year, sealed FROM snapshots")}
    report["all_snapshots_sealed"] = all(report["seals"].values())

    # 8. 验收标准（v0.2.0 扩展）
    a = {}
    a["1_真实数据源获取"] = report["coverage_real"].get("total_rows", 0) > 0
    a["2_真实覆盖率报告"] = cp.exists() and not report["coverage_real"].get("synthetic")
    a["3_历史数据清洗"] = len(list((ROOT / "data/knowledge/snapshots").glob("*/clean_data.parquet"))) >= 4
    a["4_知识库建立"] = (ROOT / "data/knowledge").exists()
    a["5_时间隔离快照"] = all(kb.is_sealed(y) for y in (2001, 2002, 2003, 2004))
    a["6_自动阻止未来数据"] = report["pytest"]["exit_code"] == 0
    a["7_多模型基准对比"] = len([v for v in report["model_comparison"].get("results", {}).values()
                                if v.get("status") == "ok"]) >= 5
    a["8_2002历史预测"] = any((ROOT / "data/predictions").glob("pred_2002*"))
    a["9_预测不可修改HMAC链"] = (report["prediction_integrity"]["ok"]
                                and "HMAC" in report["prediction_integrity"].get("mechanism", ""))
    a["10_导入2002真实数据"] = (ROOT / "data/knowledge/raw_documents/actuals/actual_2002.parquet").exists()
    a["11_误差分析"] = all((ROOT / f"data/checkpoints/year_{y}.json").exists() for y in (2002, 2003, 2004))
    a["12_更新知识库与决策库"] = len(meta.query("SELECT * FROM decision_rules")) >= 3
    a["13_2003预测"] = any((ROOT / "data/predictions").glob("pred_2003*"))
    a["14_年度循环完整"] = all(len(json.loads((ROOT / f"data/checkpoints/year_{y}.json").read_text())
                                   .get("completed", [])) == 17 for y in (2002, 2003, 2004))
    a["15_内存限制稳定"] = all((m["mem_peak_mb"] or 0) < 512 for m in report["performance"])
    a["16_完整日志与文档"] = (ROOT / "docs/DEPLOYMENT.md").exists() and (ROOT / "data/logs/system.log").exists()
    a["17_内存统计非0"] = any((m["mem_peak_mb"] or 0) > 0 for m in report["performance"])
    a["18_性能优化一致性"] = report["performance_report"].get("consistency", {}).get("all_consistent", False)
    a["19_末位快照已冻结"] = report["seals"].get("2004", False)
    a["20_最终验收PASS"] = report["final_acceptance"].get("PASS", False)
    report["acceptance"] = a
    report["acceptance_passed"] = sum(a.values())
    report["acceptance_total"] = len(a)

    out = ROOT / "data/reports/test_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "pytest": report["pytest"],
        "acceptance": f"{report['acceptance_passed']}/{report['acceptance_total']}",
        "failed": [k for k, v in a.items() if not v],
        "prediction_integrity": report["prediction_integrity"],
        "report": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
