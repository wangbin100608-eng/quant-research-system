# v0.2.0 升级交付总报告

日期：2026-11-22
基线：v0.1.0（已在 /root/工作区/股票 运行）

## 八阶段完成情况

| 阶段 | 交付物 | 关键结果 |
|---|---|---|
| 1 代码审计 | `docs/AUDIT_REPORT.md` | 4 份并行深挖实证；发现 P0：Rust 不比对 hash、预测双端篡改可绕过、主流程未做 hash 校验 |
| 2 时间隔离验证 | `docs/ISOLATION_VERIFICATION.md`、`test_isolation_v02.py`（15 项） | 三年逐一零未来数据；注入未来行/unknown/非法日期/篡改均被 Rust 阻断 |
| 3 真实数据 | `docs/REAL_DATA_REPORT.md`、5 个适配器、`coverage_report_real.json` | **12,716 行真实数据**（A股2403/美股6285/FRED3955/SEC73），缺口明确未伪造 |
| 4 知识库升级 | `kb.py`（全目录哈希/verify/restore）、`test_kb_v02.py`（6 项） | 快照全目录哈希、可恢复可重新验证、来源+available_time、严格模式 |
| 5 模型升级 | `models.py` 6 模型、`docs/MODEL_COMPARISON_REPORT.md` | 均值/时序/LightGBM/XGBoost/CatBoost/PyTorch 同一边界对比 |
| 6 性能优化 | `docs/PERFORMANCE_REPORT.md`、`perf_benchmark.py` | 修复 0.0MB；列投影 4.11x、Polars 14.78x、去重 17.53x，一致性全 true |
| 7 监控面板 | `build_dashboard_v02.py`、Streamlit 12 标签 | 真实/合成状态、覆盖率、缺失率、截止日期、Hash、泄漏测试、模型、对比、内存 |
| 8 最终验收 | `final_acceptance.json`、`test_report.json` | **PASS=true，20/20 验收，37 项测试全过** |

## 最重要的验收：时间隔离（用户反复强调）

| 预测 | 截止 | 可访问空间未来数据 | Hash | 冻结 |
|---|---|---|---|---|
| 2002 | 2001-12-31 | 0 | 一致 | ✅ |
| 2003 | 2002-12-31 | 0 | 一致 | ✅ |
| 2004 | 2003-12-31 | 0 | 一致 | ✅ |

不仅查询时过滤，而是快照物理隔离 + Rust 全量扫描 + manifest 哈希验证 + 对抗性测试实证。

## 关键技术修复（相对 v0.1）

1. **Rust TimeGuard**：严格日期元组比较（不再字符串）、manifest JSON 严格解析（sealed 必须布尔 true、缺失即失败）、逐项比对 file_hashes、IO fail-closed、NIST 自测向量。
2. **预测不可篡改**：HMAC-SHA256 链 + append-only SQLite 触发器 + 原子落盘 + 消除双份真相源；密钥 `data/.seal_key`（chmod 600，独立于数据）。
3. **2004 未冻结**：末位快照在 run_all 结束时补 seal；四年全部冻结。
4. **内存统计 0.0**：tracemalloc 引用计数 + ru_maxrss。
5. **真实数据**：与 fixture 物理分离，真实行强制 `source=real:*`、`synthetic=false`。

## 端到端执行记录（真实数据）

```
12,716 行真实数据 → 2000-2001 → 预测2002 → 冻结预测(HMAC链)
→ 导入2002实际数据 → 误差分析 → 更新知识库 → 预测2003 → ... → 2004
```
- 2002 误差：MAE≈0.36（7 标的）
- 2003 误差：MAE≈0.36
- 2004 误差：MAE≈0.36
- 年度循环各 17 步，checkpoint 可续跑
- 内存峰值：2002=74.93MB、2003=9.82MB、2004=13.13MB（真实 RSS）

## 完整交付清单

**源代码**：`python/quant/*`（含 adapters 8 个、knowledge、models、evaluation_v02、orchestrator）、`rust_core/src/main.rs`
**数据库结构**：SQLite 7 张表 + `prediction_chain`（在 `data/meta.db`）
**报告**：`docs/` 下 AUDIT / ISOLATION / REAL_DATA / MODEL_COMPARISON / PERFORMANCE / DEPLOY_RECOVERY_V02 + `data/reports/` 下 coverage_report_real / model_comparison / performance_report / final_acceptance / test_report
**部署恢复**：`docs/DEPLOY_RECOVERY_V02.md`、`scripts/backup.sh`（含密钥）
**公网页面**：https://a.czyz.eu.cc/

## 复现命令

```bash
cd /root/工作区/股票
PYTHONPATH=python python3 python/scripts/fetch_real.py
PYTHONPATH=python python3 python/scripts/run.py run --start 2002 --end 2004 --raw data/raw/raw_real.parquet
PYTHONPATH=python python3 -m pytest python/tests -q
PYTHONPATH=python python3 python/scripts/model_benchmark.py
PYTHONPATH=python python3 python/scripts/perf_benchmark.py
PYTHONPATH=python python3 python/scripts/final_acceptance.py
PYTHONPATH=python python3 python/scripts/test_report.py
PYTHONPATH=python python3 python/scripts/build_dashboard.py
```
