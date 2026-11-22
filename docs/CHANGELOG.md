# CHANGELOG

## v0.5.0（全链路防未来数据泄漏与高性能数据引擎）

- 阶段一：只读审计 `docs/V05_STAGE1_AUDIT.md`，建立基线（73 测试 / 验收 8-8 / 覆盖率 32%）。
- P0：`final_acceptance_v05.py` 真实负向闭环（模块删除、四类报告篡改、三接口数据注入），非零退出码，结构化执行日志与 commit；`prediction_freeze_v05.py` 完整溯源与修正链、append-only。
- P1：`provenance_v05.py` Embedding/模型权重/Agent 时间溯源与越权审计。
- P2：`TimeGuardReader.stream_parquet` 分批流式读取；100 万行峰值 84.6MB→10.46MB。
- P3：`data_quality_v05.py` 历史股票池快照（保留退市股）与数据质量评分。
- 验证：94 测试通过、Rust selftest PASS、v0.5 验收 4/4 PASS、覆盖率 35%。

## v0.4.0

- P0：可信验收 v04、UTC 时间内核、ALFRED 版本字段、腾讯真实指数日线、coverage 纳入验收。

## v0.3.0 / v0.2.0 / v0.1.0

见 [V03_DELIVERY_REPORT.md](V03_DELIVERY_REPORT.md) 与历史提交。
