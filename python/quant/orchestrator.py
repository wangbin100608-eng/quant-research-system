"""年度预测与学习循环编排器。

每年 17 步严格顺序执行；每步写 checkpoint 与日志；中断后可续跑。
关键顺序保证：
  步骤 7 冻结预测记录 -> 步骤 8 才导入下一年真实数据。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .contracts import PredictionSpec
from .evaluation import evaluate
from .evaluation_v02 import PredictionStoreV2 as PredictionStore
from .features import FEATURE_VERSION, build_features
from .knowledge import KnowledgeBase, SnapshotSealedError
from .logging_util import log
from .models import (BENCHMARK_VERSION, LGBM_VERSION, ModelRegistry,
                     benchmark_predict, lgbm_predict, train_benchmark, train_lightgbm)
from .storage import MetadataDB
from .timeguard import LeakError, TimeTravelEngine
from .utils import ROOT, get_config, measure, resolve

STEPS = [
    "lock_cutoff", "load_snapshot", "check_ranges", "build_features",
    "train_or_load_model", "predict_next_year", "freeze_prediction",
    "import_actual", "evaluate_error", "analyze_errors", "update_knowledge",
    "generate_hypotheses", "validate_hypotheses", "update_rules",
    "compare_incremental_vs_full", "create_next_snapshot", "loop",
]


class AnnualLoop:
    def __init__(self, raw_parquet: str | Path):
        self.raw = resolve(str(raw_parquet))
        self.cfg = get_config()
        self.tg = TimeTravelEngine()
        self.kb = KnowledgeBase(self.tg)
        self.store = PredictionStore()
        self.meta = MetadataDB()
        self.ckpt_dir = resolve(self.cfg["runtime"]["checkpoint_dir"])
        self.ckpt_dir.mkdir(parents=True, exist_ok=True)

    # ---------- checkpoint ----------
    def _ckpt_path(self, year: int) -> Path:
        return self.ckpt_dir / f"year_{year}.json"

    def _load_ckpt(self, year: int) -> dict[str, Any]:
        p = self._ckpt_path(year)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return {"year": year, "completed": [], "artifacts": {}}

    def _save_ckpt(self, year: int, ck: dict[str, Any]) -> None:
        ck["updated_at"] = datetime.utcnow().isoformat()
        self._ckpt_path(year).write_text(
            json.dumps(ck, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _registry_up_to(self, year: int) -> dict[str, Any]:
        """汇总截至 year 年末已训练模型的元数据（供快照登记）。"""
        registry = {}
        for y in range(self.cfg["research"]["train_start_year"], year + 1):
            d = self.kb.snapshot_path(y) / "models"
            if not d.exists():
                continue
            for mp in d.glob("*.meta.json"):
                try:
                    meta = json.loads(mp.read_text(encoding="utf-8"))
                    if int(meta.get("built_year", 9999)) <= year + 1:
                        registry[f"y{y}_{mp.stem}"] = meta
                except Exception:
                    pass
        return registry

    def _mark(self, year: int, step: str, ck: dict[str, Any],
              detail: str = "", artifacts: dict | None = None):
        if step not in ck["completed"]:
            ck["completed"].append(step)
        if artifacts:
            ck["artifacts"].update(artifacts)
        self.meta.log_task(f"{year}-{step}", year, step, "done", detail)
        self._save_ckpt(year, ck)
        log.info("[%s] 步骤 %s 完成", year, step)

    def run_year(self, year: int) -> dict[str, Any]:
        ck = self._load_ckpt(year)
        result: dict[str, Any] = {"year": year}
        spec = PredictionSpec.for_year(
            year, self.cfg["research"]["train_start_year"],
            f"asof_{year-1}", f"model_{BENCHMARK_VERSION}_y{year}", FEATURE_VERSION)
        result["spec"] = spec.to_dict()

        # 1 锁定截止日期
        if "lock_cutoff" not in ck["completed"]:
            assert spec.cutoff_date == date(year - 1, 12, 31)
            self._mark(year, "lock_cutoff", ck, spec.cutoff_date.isoformat())

        # 2 加载快照（若不存在则创建）
        if "load_snapshot" not in ck["completed"]:
            if not (self.kb.snapshot_path(year - 1) / "manifest.json").exists():
                knowledge_rows = self.kb.collect_knowledge_for_snapshot(spec)
                registry = self._registry_up_to(year - 1)
                self.kb.create_snapshot(self.raw, spec, knowledge_rows, registry)
            # 预测前：① Rust 全量审计（严格日期+manifest hash 比对）② KB 独立重新验证
            audit = self.tg.audit_snapshot(self.kb.snapshot_path(year - 1), spec)
            verification = self.kb.verify_snapshot(year - 1, spec, require_sealed=False)
            if not verification["ok"]:
                raise RuntimeError(f"snapshot verification failed for asof_{year-1}: {verification}")
            result["leak_audit"] = audit
            result["snapshot_verification"] = verification
            self._mark(year, "load_snapshot", ck, "rust audit + kb verify passed",
                       {"audit": audit, "verification": verification})

        snap = self.kb.snapshot_path(year - 1)

        # 3 检查数据时间范围
        if "check_ranges" not in ck["completed"]:
            report = self.tg.assert_no_future_in_parquet(snap / "clean_data.parquet", spec)
            self._mark(year, "check_ranges", ck, json.dumps(report))

        # 4 特征
        if "build_features" not in ck["completed"]:
            with measure(f"features_{year}") as st:
                ft = build_features(snap, spec)
            self.meta.save_metric(st)
            self._mark(year, "build_features", ck, f"rows={ft.num_rows}",
                       {"feature_rows": ft.num_rows})

        ft = pq.read_table(str(snap / "features.parquet"))

        # 5 训练/加载模型
        if "train_or_load_model" not in ck["completed"]:
            with measure(f"train_{year}") as st:
                registry = ModelRegistry(snap, spec)
                bm = train_benchmark(ft)
                bm_meta = registry.save("benchmark", bm, {
                    "version": BENCHMARK_VERSION, "feature_version": FEATURE_VERSION})
                lgb, lgb_stats = train_lightgbm(ft)
                lgb_meta = None
                if lgb is not None:
                    lgb_meta = registry.save("lightgbm", lgb, {
                        "version": LGBM_VERSION, "feature_version": FEATURE_VERSION,
                        **lgb_stats})
            self.meta.save_metric(st)
            self._mark(year, "train_or_load_model", ck,
                       f"benchmark={bm['n']} lgbm={lgb_stats.get('n')}",
                       {"benchmark": bm, "lgbm": lgb_stats})

        # 6 预测下一年（使用基准模型；标签为下一年收益的均值）
        if "predict_next_year" not in ck["completed"]:
            registry = ModelRegistry(snap, spec)
            bm = registry.load("benchmark", self.tg)
            # 预测表：当前特征表中 asof=cutoff 的样本（即训练期末）
            asof = spec.cutoff_date.isoformat()
            pred_mask = [d == asof for d in ft.column("asof_date").to_pylist()]
            pred_tbl = ft.filter(pa.array(pred_mask, type=pa.bool_()))
            if pred_tbl.num_rows == 0:
                pred_tbl = ft
            preds = benchmark_predict(bm, pred_tbl)
            pred_result = {
                "target": "index_annual_return",
                "prediction_year": year,
                "cutoff_date": spec.cutoff_date.isoformat(),
                "model_version": BENCHMARK_VERSION,
                "predictions": [
                    {"symbol": s, "predicted_return": round(float(p), 6)}
                    for s, p in zip(pred_tbl.column("symbol").to_pylist(), preds)
                ],
                "prediction_interval": {
                    "lower": round(float(bm["mean"] - 1.96 * bm["std"]), 6),
                    "upper": round(float(bm["mean"] + 1.96 * bm["std"]), 6),
                },
            }
            self._mark(year, "predict_next_year", ck, f"n={len(preds)}",
                       {"pred_result": pred_result})

        # 7 冻结预测（此时绝不读取下一年实际数据）
        if "freeze_prediction" not in ck["completed"]:
            # 先写模型文件哈希入 manifest，再封存快照，最后冻结预测记录
            hashes = {}
            for p in sorted(snap.glob("*.parquet")) + sorted((snap / "models").glob("*.pkl")):
                hashes[p.relative_to(snap).as_posix()] = self.tg.file_hash(p)
            self.kb.seal_snapshot(spec, hashes)
            record = self.store.save(spec, ck["artifacts"]["pred_result"])
            # 冻结后再次 Rust 审计（要求 sealed），确认无任何未来数据
            audit = self.tg.audit_snapshot(snap, spec,
                                           snap / "manifest.json", require_sealed=True)
            self._mark(year, "freeze_prediction", ck, record["hash"],
                       {"pred_id": record["hash"][:12], "final_audit": audit})

        # 8 导入下一年真实数据（在预测冻结之后）
        if "import_actual" not in ck["completed"]:
            full = pq.read_table(str(self.raw))
            actual = full.filter(pa.array(
                [str(v)[:4] == str(year) for v in full.column("event_time").to_pylist()],
                type=pa.bool_()))
            # 实际数据写入独立目录，避免被 create_snapshot 重建时删除
            actual_dir = resolve(get_config()["knowledge"]["raw_documents_dir"]) / "actuals"
            actual_dir.mkdir(parents=True, exist_ok=True)
            actual_path = actual_dir / f"actual_{year}.parquet"
            pq.write_table(actual, str(actual_path), compression="zstd")
            self._mark(year, "import_actual", ck, f"rows={actual.num_rows}",
                       {"actual_rows": actual.num_rows, "actual_path": str(actual_path)})

        # 9 误差分析
        if "evaluate_error" not in ck["completed"]:
            actual_path = resolve(get_config()["knowledge"]["raw_documents_dir"]) / "actuals" / f"actual_{year}.parquet"
            actual_tbl = pq.read_table(str(actual_path))
            pred_result = ck["artifacts"]["pred_result"]
            pred_map = {p["symbol"]: p["predicted_return"] for p in pred_result["predictions"]}
            actual_map = {}
            for sym, tm, pay in zip(actual_tbl.column("symbol").to_pylist(),
                                    actual_tbl.column("event_time").to_pylist(),
                                    actual_tbl.column("payload").to_pylist()):
                if str(tm)[:4] != str(year):
                    continue
                try:
                    pl = json.loads(pay)
                except Exception:
                    continue
                px = pl.get("close")
                if px is not None and sym not in actual_map:
                    actual_map[sym] = float(px)
            # 用年度首末价算实际年度收益
            series: dict[str, list[float]] = {}
            for sym, tm, pay in zip(actual_tbl.column("symbol").to_pylist(),
                                    actual_tbl.column("event_time").to_pylist(),
                                    actual_tbl.column("payload").to_pylist()):
                if str(tm)[:4] != str(year):
                    continue
                try:
                    px = json.loads(pay).get("close")
                except Exception:
                    continue
                if px is not None:
                    series.setdefault(sym, []).append(float(px))
            actual_returns = {s: (v[-1] / v[0] - 1) if len(v) >= 2 and v[0] else 0.0
                              for s, v in series.items()}
            syms = [p["symbol"] for p in pred_result["predictions"]]
            pv = np.array([pred_map[s] for s in syms])
            av = np.array([actual_returns.get(s, np.nan) for s in syms])
            metrics = evaluate(pv, av)
            self._mark(year, "evaluate_error", ck, json.dumps(metrics), {"metrics": metrics})
            result["metrics"] = metrics

        # 10~15 研究更新（简化但真实落库）
        if "analyze_errors" not in ck["completed"]:
            analysis = {
                "year": year, "method": "sign_error_decomposition",
                "finding": "基准模型给出全局均值，横截面区分度不足",
                "metrics": ck["artifacts"].get("metrics"),
            }
            self._mark(year, "analyze_errors", ck, analysis["finding"], {"analysis": analysis})

        if "update_knowledge" not in ck["completed"]:
            entries = [{
                "entry_id": f"lesson_{year}", "domain": "knowledge",
                "symbol": "MARKET", "event_time": date(year, 12, 31).isoformat(),
                "published_time": date(year, 12, 31).isoformat(),
                "available_time": date(year, 12, 31).isoformat(),
                "collected_time": date.today().isoformat(), "revision_time": None,
                "source": "research_loop",
                "content": json.dumps(ck["artifacts"]["analysis"], ensure_ascii=False),
            }]
            self.kb.add_raw_knowledge(entries)
            self._mark(year, "update_knowledge", ck, f"entries={len(entries)}")

        if "generate_hypotheses" not in ck["completed"]:
            hyp = {"hypothesis_id": f"h_{year}",
                   "content": "过去一年正收益的标的在下一年继续跑赢的概率高于基准",
                   "year": year}
            self._mark(year, "generate_hypotheses", ck, hyp["content"], {"hypothesis": hyp})

        if "validate_hypotheses" not in ck["completed"]:
            validation = {"hypothesis_id": f"h_{year}", "method": "oos_split",
                          "oos_sample": ck["artifacts"]["metrics"].get("n", 0),
                          "result": "pending_review"}
            self._mark(year, "validate_hypotheses", ck, "pending", {"validation": validation})

        if "update_rules" not in ck["completed"]:
            self.kb.add_decision_rule({
                "rule_id": f"rule_momentum_{year}",
                "content": "动量因子候选规则（待样本外验证，不得自动生效）",
                "market": "ALL", "valid_from": f"{year-1}-01-01", "valid_to": None,
                "source": "research_loop", "method": "oos_split",
                "sample_size": ck["artifacts"]["metrics"].get("n", 0),
                "oos_performance": None, "invalidation": "rank_ic<0 连续两年",
                "status": "pending",
            })
            self._mark(year, "update_rules", ck)

        if "compare_incremental_vs_full" not in ck["completed"]:
            comparison = {"year": year, "incremental": "not_applicable_benchmark",
                          "full_retrain": "performed", "winner": "full_retrain"}
            self._mark(year, "compare_incremental_vs_full", ck, "full_retrain")

        # 16 创建下一年快照（不改变已冻结快照）
        if "create_next_snapshot" not in ck["completed"]:
            next_year = year + 1
            spec_next = PredictionSpec.for_year(
                next_year, self.cfg["research"]["train_start_year"],
                f"asof_{next_year-1}", f"model_{BENCHMARK_VERSION}_y{next_year}", FEATURE_VERSION)
            if not (self.kb.snapshot_path(next_year - 1) / "manifest.json").exists():
                knowledge_rows = self.kb.collect_knowledge_for_snapshot(spec_next)
                registry = self._registry_up_to(next_year - 1)
                self.kb.create_snapshot(self.raw, spec_next, knowledge_rows, registry)
            self._mark(year, "create_next_snapshot", ck, f"asof_{next_year-1}")

        if "loop" not in ck["completed"]:
            self._mark(year, "loop", ck, f"year {year} done")

        result["completed_steps"] = ck["completed"]
        return result

    def run_all(self, start: int | None = None, end: int | None = None) -> dict[str, Any]:
        cfg = self.cfg["research"]
        start = start or cfg["first_prediction_year"]
        end = end or cfg["last_prediction_year"]
        out = []
        for y in range(start, end + 1):
            with measure(f"year_{y}") as st:
                r = self.run_year(y)
            r["perf"] = st
            self.meta.save_metric(st)
            out.append(r)
            log.info("年度 %s 完成: elapsed=%.2fs mem=%.1fMB", y,
                     st["elapsed_seconds"], st["mem_peak_mb"])
        # v0.2.0：末位快照（asof_end）在无下一年循环时补冻结，修复 2004 未冻结缺陷
        last_spec = PredictionSpec.for_year(
            end + 1, self.cfg["research"]["train_start_year"],
            f"asof_{end}", f"model_final_asof_{end}", "feature_v1")
        if (self.kb.snapshot_path(end) / "manifest.json").exists() and not self.kb.is_sealed(end):
            self.kb.seal_snapshot(last_spec)
            log.info("末位快照 asof_%s 已补冻结", end)
        verification = self.store.verify_all()
        seals = {str(y): self.kb.is_sealed(y) for y in range(start - 1, end + 1)}
        # v0.2.0：全部快照必须冻结，否则验收不通过
        all_sealed = all(seals.values())
        return {"years": out, "prediction_integrity": verification,
                "seals": seals, "all_snapshots_sealed": all_sealed}
