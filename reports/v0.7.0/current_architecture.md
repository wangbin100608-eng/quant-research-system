# v0.7.0 P0 当前架构审计报告

审计日期：2026-11-22 ｜ 基线：v0.6.0，HEAD `983cdc8`，工作区干净。

## 1. 目录结构

```
config/       config.yaml, sources.yaml（2 个）
python/quant/ 89 个 Python 文件，约 10,256 行
  adapters/    18 个适配器文件
  knowledge/   kb.py（快照冻结/恢复）
  dashboard/   Streamlit app
  scripts/     数据/验证/性能/验收/报告脚本
rust_core/    src/main.rs 497 行（TimeGuard CLI）
data/         raw / knowledge / predictions / reports / checkpoints / backups
tests/        9+ 个测试文件，121 项测试
docs/         v0.2-v0.6 交付/审计报告
web/          dashboard.html / index.html（公网 a.czyz.eu.cc）
```

## 2. 核心模块边界

| 层 | 模块 | 状态 |
|---|---|---|
| 时间隔离 | `timeguard.py`、`timeguard_reader.py`、`timestamps_v04.py`、Rust `main.rs` | 已有，持续强化至 v0.6 |
| 数据 | `adapters/*`（18）、`coverage.py`、`fetch_v03.py`、`fetch_v04_indices.py` | 已有 |
| 知识库 | `knowledge/kb.py`、`knowledge_v03.py`、`provenance_v05.py`、`enforcement_v060.py` | 已有 5 库 kb_items + hypotheses 独立表 |
| 特征/标签 | `features.py`、`horizons.py`（7 期限） | 已有 |
| 模型 | `models.py`（6 模型）、`validation.py`（5 split） | 已有 |
| 预测/冻结 | `evaluation_v02.py`、`prediction_freeze_v05.py` | 已有 HMAC 链 |
| 验收 | `final_acceptance_v05.py` + 4 个 v0.4.1 组件 | 已有 6 项检查 |
| 调度 | `orchestrator.py`（年度循环 17 步） | 已有，无统一研究调度器 |

## 3. 与 v0.7.0 架构差异

- 现有代码在 `python/quant/`，v0.7.0 目标 `src/` 分层（data/universe/features/labels/hypothesis/experiments/statistics/validation/research_memory/models/scheduler/forecasting/shadow_mode/monitoring/reporting）——尚未建立，需迁移计划（见 migration_plan.md）。
- 配置仅 2 个 YAML，v0.7.0 规划 8 个（system/data_quality/feature_weights/model_config/validation/resource_policy/shadow_mode 等）——需补齐。
- 研究记忆仅 SQLite 基础表（hypotheses/experiments 尚未建表），FTS5 未启用。
- 无独立 scheduler / shadow_mode / reporting 模块。

## 4. 基线验证

- pytest：121 passed（exit 0）。
- Rust selftest：PASS。
- v0.5 验收：6/6 PASS，exit 0。
- 快照 4 个、冻结预测 3 条、年度循环 3×17 步。

结论：现有系统是可用基线，v0.7.0 应以"复用 + 增量扩展"方式推进，不重写已有模块。
