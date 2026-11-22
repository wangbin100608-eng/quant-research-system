# v0.7.0 P0 迁移计划（v0.6 → v0.7）

## 原则

- 复用优先：保留 `python/quant` 全部现有模块，通过 `src/` 薄封装逐步迁移，不一次性重写。
- 不删历史：历史报告、快照、预测、审计日志原样保留。
- 每阶段独立 commit + 测试。

## 阶段映射

| v0.7 阶段 | 复用现有 | 新增/改造 |
|---|---|---|
| P1 架构 | config、storage、utils | 建 src/ 软链接或包重定向；新增 6 个 YAML 配置 |
| P2 Provider | 18 个 adapters | 抽象 DataProvider 基类；Tushare/Alpaca 需官方权限核验后再实现，未授权标 BLOCKED |
| P3 存储 | raw_v03 parquet、coverage | raw/{market}/{provider}/{dataset}/{date} 分区 + 原始响应哈希 + 断点续传 |
| P4 PIT | timeguard/timeguard_reader/timestamps/enforcement | 业务直读收编至 TimeGuardReader；每入口注入测试 |
| P5 Feature | features.py/horizons.py | Feature Factory + registry；补横截面/财务/宏观特征 |
| P6 权重 | 无 | feature_weights.yaml（三类期限）；四种权重分离 |
| P7 Label | horizons.py（7 期限） | Label Factory（1d/5d/20d + 横截面排名）+ label_version |
| P8-P9 AI 假设 | agents.py stub + hypotheses 表 | 结构化假设 schema + 去重 + 权限沙箱 |
| P10-P12 实验/统计/验证 | validation.py + stats_v060 | Experiment Engine + HAC/BH/Block Bootstrap + 独立最终测试集 |
| P13 Research Memory | SQLite hypotheses/kb_items | 9 张表 + FTS5；历史结论 append-only |
| P14-P15 资源/调度 | orchestrator.py | Resource Manager + 研究调度器（预算配置） |
| P16-P17 模型/Shadow | models.py（6 模型） | 市场×周期 6 组 + Ridge/ElasticNet/Ranker + Shadow Mode |
| P18 Dashboard | web/dashboard + Streamlit | 数据/特征/假设/实验/预测 5 页 |
| P19 测试 | 121 tests | Provider/研究/预测闭环测试 + 质量门禁 |

## 数据迁移与回滚

- 数据库新增表用 `CREATE TABLE IF NOT EXISTS`；迁移脚本 forward/rollback（已有 `migrate_v04.py` 模式）。
- 原始 parquet 不覆盖：新分区目录与旧 raw_v03 并存，manifest 记录双路径。
- 配置新增不修改旧 config.yaml 语义。

## 风险与前置条件

1. Tushare Pro / Alpaca 需 token/订阅权限，未核验前一律 BLOCKED，不得声称接入。
2. Git 历史旧密钥已公开（见 security_audit）；正式安全声明前需人工决定是否重写历史。
3. 分钟数据与订单簿数据当前完全缺失，微观结构特征在未获取数据前禁止实现。
4. 1000万/5000万行压测需更高内存环境。

## P0 后动作

输出九份报告 → 暂停等待人工检查 → 确认后进入 P1（统一架构与配置）。
