# v0.7.0 P0 未来数据泄漏审计报告

## 1. 现有防线（实际存在且经测试）

- 五时间字段：event_time / published_at / available_at / ingested_at / revision_time（contracts/timestamps_v04）；缺 ingested_at 不伪造（unknown），日期精度标记，严格模式截止日当天保守拒绝。
- 统一门禁：`TimeGuardReader`（read_parquet 列投影自动读时间列、stream_parquet 分批审计、query_sql 强制 cutoff）、Rust `timeguard`（流式 .timeindex 审计、manifest hash 比对、NIST selftest）。
- 快照物理隔离：asof_YYYY、manifest SHA-256、sealed、append-only。
- 六库查询强制 available_time + snapshot_id；向量/模型/Agent 强制门禁（enforcement_v060）。
- 股票池 PIT（v0.6）：每年 cutoff，退市双时间校验，未来退市信息不进入早期池。
- 负向测试：Parquet/DuckDB/SQLite/KB 未来注入、列投影绕过、报告篡改、模块删除、快照篡改、HMAC 链篡改全部有测试。

## 2. 仍存在的泄漏风险面

1. **业务代码直读**：`orchestrator.py:135/199/214`、`features.py:36`、`horizons.py:50`、`knowledge/kb.py` 等仍直接 `pq.read_table`，虽依赖快照物理隔离，但未统一走 TimeGuardReader（审计已记录，v0.4 列为 P0 但未完全消除）。
2. **估算时间数据**：FRED fred_v2 的 estimated 发布时间依赖严格模式拦截；若调用方关闭 strict 则可能进入分析。
3. **历史指数成分/行业**：仅当前名单（标 BLOCKED），若研究误用会引入前视偏差。
4. **Agent/LLM**：默认确定性 stub，但外部 LLM 可注入；越权测试存在，实际接入真实 LLM 时需重新审计。

## 3. 结论

现有系统对已知泄漏路径有实质防护（非仅配置检查），但并非所有数据读取入口都经过统一 TimeGuardReader；v0.7.0 P4 应完成"全入口门禁化"并为每个入口保留独立注入测试。
