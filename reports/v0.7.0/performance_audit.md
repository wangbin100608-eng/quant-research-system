# v0.7.0 P0 性能审计报告

## 1. 架构

- Rust：timeguard CLI（审计/哈希/UTC 解析），497 行，编译为 release 二进制。
- Python：模型训练、特征、AI Agent、研究分析（89 文件，~10k 行）。
- DuckDB/Polars/Arrow：列式查询与分批读取。
- SQLite：元数据、预测、快照、审计。

## 2. 实测数据（已有报告）

| 测试 | 数据规模 | 结果 |
|---|---|---|
| 列投影 vs 全量（v0.3） | 44k 行 | 4.11x |
| DuckDB 聚合（v0.3） | 44k 行 | 1.73x |
| Polars 去重（v0.3） | 44k 行 | 17.53x |
| 流式 vs 全量（v0.5） | 100 万行 | 内存峰值 84.6MB→10.46MB，结果一致 |
| 大规模流式（v0.6） | 100 万行 | RSS 208.1MB（增量 58.68MB），13.5s |
| 大规模流式（v0.6） | 500 万行 | RSS 208.1MB（增量 0），53.8s |
| 数据质量分批（v0.6） | 44k 行 | 评分 77.8，Arrow RecordBatch |

## 3. 已知约束

- 1000 万 / 5000 万行压力测试 BLOCKED（本机 1GB 内存）。
- 数据质量仍逐行解析 payload JSON（`data_quality_v05.py`），虽已分批但 Python 循环仍是瓶颈。
- `read_parquet` 仍用 to_pylist 构建 keep 索引；`stream_parquet` 每批同理（v0.7 P4 计划 Arrow 原生过滤）。

## 4. 结论

低内存架构已初步验证（500 万行 RSS 稳定），但完整 Arrow 原生过滤与 1000 万+ 压测未完成；v0.7.0 P14 需补真实 RSS/CPU/磁盘/一致性报告。
