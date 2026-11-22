# v0.4.0 审计报告（第一步：仅审计与基准测试）

审计基线：Git commit `997ffd795c91770ae6e82255d8648586e54e2f44`（README v0.3.0）。
本阶段**未修改任何源码、测试、配置、数据、报告**；基准测试结果写入 `/tmp/v04_baseline_pytest.txt`，历史报告未覆盖。

---

## 0. 当前项目架构图

```
GitHub repo: wangbin100608-eng/quant-research-system (public, v0.3.0)
├─ config/                 config.yaml (version 0.3.0), sources.yaml
├─ rust_core/              src/main.rs — TimeGuard CLI (audit/hash/selftest)
├─ python/quant/
│  ├─ contracts.py         五时间字段契约 (event/published/available/collected/revision_time)
│  ├─ timeguard.py         Python 时间隔离引擎（validate_row/audit_snapshot/assert_no_future_in_parquet）
│  ├─ adapters/            13 个适配器：fixture/fred/fred_v2/alfred_vintage/eastmoney_cn/
│  │                       eastmoney_expand/yahoo_us/akshare_cn/akshare_extended/sec/sec_v2/
│  │                       sec_v3/news_v2 + base/coverage
│  ├─ storage.py           SQLite(7+2表) + DuckDB + Parquet 读写
│  ├─ knowledge/kb.py      快照 create/seal/load/verify/restore（SHA-256 manifest）
│  ├─ knowledge_v03.py     六库统一系统（kb_items 5库 + hypotheses 独立表）
│  ├─ agents.py            8 个 AI Agent（LLM stub，可注入）
│  ├─ horizons.py          7 期限样本生成（1d/5d/20d/1m/3m/6m/1y）
│  ├─ validation.py        5 种 split + 扩展指标 + cross_validate
│  ├─ features.py/models.py/evaluation.py(evaluation_v02)/orchestrator.py
│  └─ logging_util.py/utils.py
├─ python/scripts/         fetch_v03 / run / validation_report / perf_v03 / final_acceptance_v03 /
│                          test_report_v03 / build_dashboard / backup / alert
├─ python/tests/           9 个测试文件，59 项测试
├─ docker/ deploy/ docs/ web/
└─ data/                   raw_v03.parquet(44,021行) / predictions(HMAC链) / snapshots(4) / reports
```

数据流：`真实适配器 → raw_v03.parquet → 快照(asof_YYYY) → TimeGuard 审计 → 特征/多期限样本 → 验证/模型 → 预测冻结(HMAC链) → 导入真实 → 评估 → 知识更新`。

---

## 1. 测试执行结果（v0.3.0 基准）

| 项目 | 结果 |
|---|---|
| 命令 | `PYTHONPATH=python python3 -m pytest python/tests -q` |
| 结果 | **59 passed, 51 warnings, 4.68s**（exit 0） |
| Rust selftest | `SELFTEST: PASS` |
| 历史报告 | 未修改；测试结果仅存 `/tmp/v04_baseline_pytest.txt` |
| 覆盖率统计 | **NOT_TESTED**：`coverage` 包未安装，当前无覆盖率报告 |

测试分布：test_isolation_v02(9)、test_leakage(13)、test_validation_v03(7)、test_kb_v02(6)、test_knowledge_v03(6)、test_security_v03(5)、test_macro_vintage_v03(4)、test_temporal_semantics(3)。

---

## 2. 文件级问题清单（P0 优先）

### P0-1 自动验收系统（`python/scripts/final_acceptance_v03.py`）

| # | 位置 | 严重程度 | 问题与证据 |
|---|---|---|---|
| V1 | L31 | **P0** | `len(ks.query("document", date(2001,12,31))) >= 0` 永远成立；实测即使 0 行也 PASS。未验证时间隔离。 |
| V2 | L54-55 | P0 | 仅检查 `len(libs) >= 5`，不验证每个库**有数据**及时间范围。实测 kb_items 仅 5 库有行（hypothesis 是独立表），但第6库无检查。 |
| V3 | L63-66 | P0 | "可复现"只检查 4 个报告文件 `exists()`，不读内容；篡改 `test_report_v03.json` 的 pytest summary 仍 PASS。 |
| V4 | L70 | P0 | "内存稳定"只读报告 JSON，不实际测量；报告可伪造。 |
| V5 | L47 | P0 | "误差分析"只检查 3 个 checkpoint 文件存在，不读内容/不验证指标。 |
| V6 | L39-44 | P0 | Walk-forward / 多模型对比只读 JSON 键名，不重新执行验证；报告可伪造。 |
| V7 | L73 | P1 | `len(build_agents(ks)) == 8` 只数对象，不验证 Agent 行为与时间门禁。 |
| V8 | L74-76 | P1 | 密钥检查用 `git ls-files`，只查当前索引，不查完整 Git 历史。 |
| V9 | 全文 | P0 | 14 项检查均无"输入/执行过程/结果/失败原因"结构化输出；异常被抛出即整个脚本崩溃（非结构化 FAIL）。 |
| V10 | 全文 | P0 | 无负向测试：删除/禁用 TimeGuard、KB、validation 模块后脚本仍可能 PASS（因多数检查读文件/JSON）。 |

### P0-2 统一时间隔离内核

| # | 位置 | 严重程度 | 问题与证据 |
|---|---|---|---|
| T1 | contracts.py L4-11 / adapters/base.py L22-26 | **P0** | 时间字段命名为 `published_time/available_time/collected_time`，与 v0.4 要求的 `published_at/available_at/ingested_at` 不一致；无 UTC 时区标记（全为 naive date/datetime.utcnow()）。 |
| T2 | orchestrator.py / contracts.py | **P0** | cutoff 统一为 `date(Y-1,12,31)`，非 `Y-1-12-31 23:59:59 UTC`；无时区转换逻辑。 |
| T3 | horizons.py L50 / orchestrator.py L199 / knowledge/kb.py L73 / features.py L36 | **P0** | 业务代码直接 `pq.read_table` 原始数据/快照，未强制经过 TimeGuard（部分由快照物理隔离间接保证，但无统一强制接口）。 |
| T4 | rust_core/src/main.rs + timeguard.py | P1 | Rust 与 Python 的 unknown 处理/日期解析语义在 v0.2 已对齐过，但 v0.4 新增 `available_at` 字段后需重新统一测试。 |
| T5 | agents.py L47-52 | P1 | Agent 有 call_log（cutoff/snapshot_id/input_summary），但上下文未强制经过 TimeGuard；无越权访问测试。 |
| T6 | knowledge_v03.py L100-120 | P1 | 查询按 available_time + snapshot_id 过滤，但未使用 v0.4 要求的 `available_at` 名称；未来数据写入后历史查询不可见（已测），但缺 UTC 处理。 |

### P0-3 A股真实行情

| # | 位置 | 严重程度 | 问题与证据 |
|---|---|---|---|
| D1 | data/reports/a_share_price_gap.json | **P0/BLOCKED** | 上证/深证/沪深300/中证500/中证1000 **均无 v0.3 真实日线**：东财 kline 限流；v0.2 的 A股日线是 fixture，未混入（正确）。 |
| D2 | eastmoney_expand.py / coverage_report_v03.json | P0 | 无真实个股日线、成交量/成交额、换手率、复权因子、代码变更记录。 |
| D3 | akshare_extended.py | P1 | 退市股 374 条有真实终止日期；指数成分 850 条为**当前名单**（payload 标 current_members），非历史成分变化。 |
| D4 | 全适配器 | P1 | 无原始文件 SHA-256 保存、无缺失率统计、无异常价格检查。 |
| D5 | coverage_report_v03.json | P1 | 缺"覆盖标的数量/更新时间/真实性标记/SHA-256"完整字段。 |

### P0-4 宏观历史版本

| # | 位置 | 问题 |
|---|---|---|
| M1 | alfred_vintage.py | 已保存 vintage/原始值/revision_time，严格模式禁 estimated（4 项测试通过）——较完整。 |
| M2 | alfred_vintage.py | 未显式记录"首次发布日期"与"历史版本号"字段（只有 vintage_date/header 中的版本后缀）。 |

### P1/P2 其他

- 六库：`kb_items` 实际 5 库有数据（document 4/event 1/rule 1/structured 1/vector 1），hypothesis 为独立表 4 条；其中 document 含 `available_time=2002-05-01`（测试写入的未来数据），需清理测试污染。
- Agent：无数据访问日志持久化、无引用来源、无越权/幻觉检查测试。
- 可复现性：预测记录有 HMAC 链、feature_version，但**无 git commit / data_hash / model_hash / 依赖版本 / 随机种子 / 运行环境 / 冻结时间**字段。
- 密钥安全：完整 Git 历史中 v0.2 提交曾含 `.seal_key`（已轮换+从索引移除，但历史记录仍在）；当前索引干净；未检查 Docker 镜像层/CI 日志（BLOCKED：无相应环境）。
- 性能：`perf_v03.py:86` 与 `data_pipeline.py:78` 用 `read_bytes()` 整体读文件算哈希（P2）；无 Arrow RecordBatch/分批下载/增量更新。
- README/版本/报告：README 已 v0.3.0，与 config/rust/README 一致；`final_acceptance_v03.json` 14/14 PASS 含上述漏洞（需以 v0.4 严格验收替代）。

---

## 3. P0 修复计划（等待人工确认后执行）

1. **新建 `final_acceptance_v04.py`（不改历史 v0.3 报告）**：每项检查输出 `{input, execution, result, failure_reason}`；真实重新执行 pytest 并解析数量；篡改报告 JSON 必须 FAIL；核心模块缺失必须 FAIL；任一 P0 失败整体 FAIL；增加负向测试（在临时副本删除模块/篡改报告验证 FAIL）。
2. **统一时间内核 v0.4**：新增 `TimeStampV04`（UTC datetime）与字段迁移 `published_at/available_at/ingested_at`；cutoff 改为 `Y-1-12-31T23:59:59Z`；所有读取走统一 `TimeGuardReader`；Rust/Python 共用时间规则测试；提供 SQLite/Parquet schema 迁移与回滚脚本。
3. **补 A 股真实行情**：待东财限流解除后分批抓取 5 指数 + 个股日线（含成交量/额/换手/复权）；若持续 BLOCKED，明确记录并保留缺口，不用合成补齐。
4. **宏观版本完善**：ALFRED 增加首次发布日期/版本号字段；严格查询按 cutoff 返回当时版本。
5. **新增覆盖率统计**：安装 `coverage` 并输出覆盖率报告，纳入验收。

建议小步提交顺序：验收脚本 → 时间内核 → 宏观 → A股数据 → 其他 P1/P2，每个模块独立 commit 与测试。

---

## 4. 预计修改文件列表（供确认）

**新增**：`python/scripts/final_acceptance_v04.py`、`python/quant/timestamps_v04.py`、`python/quant/timeguard_reader.py`、`python/tests/test_acceptance_v04.py`、`python/tests/test_time_v04.py`、`docs/V0.4.0_DELIVERY_REPORT.md`、数据库迁移脚本 `python/scripts/migrate_v04.py`。

**修改**：`python/quant/contracts.py`、`python/quant/timeguard.py`、`rust_core/src/main.rs`、`python/quant/adapters/base.py`、`python/quant/adapters/alfred_vintage.py`、`python/quant/adapters/eastmoney_expand.py`、`python/quant/knowledge_v03.py`、`python/quant/agents.py`、`python/quant/horizons.py`、`python/quant/validation.py`、`python/quant/evaluation_v02.py`、`python/scripts/fetch_v03.py`、`python/scripts/perf_v03.py`、`.gitignore`（如需要）、`README.md`（最终一致性）。

**不改（本阶段）**：任何历史报告 JSON、data/ 下敏感资产、已推送 Git 历史。

---

## 5. 状态汇总

| 检查 | 状态 |
|---|---|
| v0.3.0 基准测试（59 项） | PASS（已执行） |
| Rust selftest | PASS（已执行） |
| 覆盖率统计 | NOT_TESTED（coverage 未安装） |
| A股指数/个股真实日线 | BLOCKED（东财限流，外部条件） |
| Docker 镜像层/CI 日志密钥检查 | NOT_TESTED（无对应环境） |
| 自动验收系统 | FAIL（存在 P0 漏洞，见 V1-V10） |
| 其余审计项 | 已审计，见问题清单 |
