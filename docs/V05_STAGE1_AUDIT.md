# v0.5.0 阶段一审计报告（只读）

基线：commit `22ef730aca9b66884dcb1666183bde1d69dc59f8`（README v0.4.0）。
本阶段未修改任何代码/数据/报告；所有结果来自实际执行与源码审计。

## 1. 当前测试结果（实际执行）

| 检查 | 结果 |
|---|---|
| `PYTHONPATH=python python3 -m pytest python/tests -q` | 73 passed, 54 warnings, 4.95s |
| `rust_core/target/release/timeguard selftest` | PASS |
| `python/scripts/final_acceptance_v04.py` | overall=PASS, 8/8, p0_failed=[] |
| coverage（v0.4 报告） | 32% |

## 2. 数据访问入口审计（调用链与绕过）

### 2.1 受 TimeGuard 管理的入口
- `timeguard_reader.py:69` `TimeGuardReader.read_parquet()` — 列投影 + available_at<=cutoff + 审计；已被 `final_acceptance_v04.py` 使用。
- `timeguard.py:104` `assert_no_future_in_parquet()`、`audit_snapshot()` — Rust/Python 全量扫描。
- `knowledge_v03.py` 六库查询 — 按 available_time + snapshot_id 过滤。
- Rust `timeguard` CLI — 流式 .timeindex 审计与 SHA-256。

### 2.2 绕过 TimeGuard 的业务读取入口（P0）

| 文件:行号 | 读取对象 | 问题 |
|---|---|---|
| `features.py:36` | `clean_data.parquet` | 快照目录读取，未经过 TimeGuardReader；依赖物理隔离无显式 cutoff 参数。 |
| `orchestrator.py:135` | `features.parquet` | 直接读取特征缓存；无 cutoff 参数。 |
| `orchestrator.py:199` | `self.raw`（raw_v03/raw_real） | **直接读取原始全量数据**，虽在预测冻结后调用，但无 TimeGuard 门禁。 |
| `orchestrator.py:214` | `actual_*.parquet` | 实际数据读取无 cutoff（设计上揭晓后可读，但缺审计记录）。 |
| `horizons.py:50` | `raw_v03.parquet` | 多期限样本生成直接读原始全量；无 cutoff 参数。 |
| `knowledge/kb.py:73,279,280` | raw / 快照 parquet | 快照创建/加载内部读取；load 有 hash 校验但路径不经过 TimeGuardReader。 |
| `storage.py:143,159` | Parquet 通用读取 / DuckDB view | 通用工具，无 cutoff；调用方需自行保证。 |
| `scripts/model_benchmark.py:29`、`scripts/perf_benchmark.py`、`scripts/perf_v03.py` | raw parquet | 分析脚本直读（基准测试用途，非预测业务路径）。 |

结论：存在 8+ 处业务读取未强制经过统一 TimeGuard；阶段二需引入 `TimeGuardReader` 强制 cutoff 参数并接入这些入口。

## 3. 自动验收器漏洞（P0）

`final_acceptance_v04.py` 已修复 v0.3 的 `len>=0`，但仍有 v0.5 要求的新漏洞面：
- 模块删除测试（1.1）只在**临时副本**用 `find_spec` 做路径校验，未真正删除模块并重跑验收（check 03 未真实执行"删除→FAIL→恢复→PASS"闭环）。
- 报告篡改测试（1.2）只改了 pytest summary 的 passed 数，**未覆盖修改测试总数、时间戳、Git Commit** 三类篡改。
- 数据注入测试（1.3）只验证 reader 剔除未来行，未验证"通过其他查询接口（SQL/KB/Agent）访问"的绕过路径。
- 验收输出未记录每项测试的**开始/结束时间、执行环境、代码 Commit**。
- 验收失败时脚本仍 `sys.exit(0)`（main 只 print），**未满足"非零退出码"要求**。

## 4. 预测冻结溯源缺口（P1）

`evaluation_v02.py` 已记录 HMAC 链、spec/result、feature_version，但缺：
- `prediction_id` 唯一显式字段（当前 pred_id 由 hash 派生）、`code_commit`、`data_snapshot_id`、
  `training_max_available_at`、`prediction_cutoff`、`frozen_at`、`model_hash`、修正版本链与修正原因。
- 禁止覆盖/删除已冻结预测由 append-only 触发器实现（已测），但"修正生成新版本+原因"未实现。

## 5. 知识库/Embedding/Agent 溯源（P1）

- 六库 `kb_items` 有 source/published_time/available_time/version/snapshot_id，但无 `ingested_at`/`revision_time`/`source_max_available_at`/`snapshot_cutoff`/`code_hash`；未建立库间引用关系与一致性审计。
- `vector` 库 embedding 无 doc_id/模型版本/生成时间/向量库版本；无召回防泄漏测试。
- Agent `call_log` 在内存（不持久化），无工具调用/文档 ID/数据快照/切 cutoff 强制；无越权测试。

## 6. 性能与内存

- `timeguard_reader.py:80` 整体 `pq.read_table` + `to_pylist()`，未用 `ParquetFile.iter_batches`/RecordBatch；阶段四需流式重构并做 100 万/500 万/1000 万行基准。
- v0.4 performance 报告仅 44k 行；缺大规模压力测试。

## 7. 阶段二 P0 修复清单（按依赖顺序）

1. `final_acceptance_v05.py`：真实负向测试闭环（删除模块/四类报告篡改/多接口注入）、非零退出码、结构化执行日志（起止时间/环境/commit）、机器可读 JSON。
2. `timeguard_access.py`：全项目读取强制走 TimeGuardReader（cutoff 必传），新增"无 cutoff 访问自动检测"测试。
3. `prediction_freeze_v05.py`：预测冻结完整溯源字段 + 修正版本链 + 独立验证 CLI。
4. 六库/Embedding/Agent 溯源字段与越权测试（阶段三）。

## 8. 阶段一结论

基线可执行（73 测试/验收 8-8/Rust PASS）；P0 缺口集中在：验收负向测试真实性、TimeGuard 绕过、预测冻结溯源、流式读取。按阶段推进，每阶段独立 commit。
