# v0.7.0 P0 缺失组件清单（对照 P1-P20）

## 完全缺失

| 组件 | 对应阶段 | 说明 |
|---|---|---|
| `src/` 目录结构 | P1 | 现为 python/quant 扁平结构 |
| 8 个配置文件 | P1 | 现仅 config.yaml/sources.yaml |
| DataProvider 统一接口 | P2/P3 | 18 个适配器各自为政 |
| Tushare/Alpaca Provider | P2 | 未接入（权限/API 未核验） |
| 原始不可变分区存储 | P3 | 无 {market}/{provider}/{dataset}/{date} 结构 |
| 断点续传/增量更新标准实现 | P3 | 部分适配器雏形 |
| Feature Factory + feature registry | P5 | 仅 6 特征硬编码 |
| 分钟/周/月频率数据 | P2/P5 | 无 |
| 微观结构特征 | P5 | 无订单簿数据，禁止虚构 |
| feature_weights.yaml 与四类权重区分 | P6 | 无 |
| Label Factory（4 类标签+版本） | P7 | 仅 horizons 衍生收益 |
| AI 假设引擎（结构化输出+权限） | P8 | 仅 agents.py stub（假设写入 pending） |
| 假设去重系统 | P9 | 无 |
| Experiment Engine（统一入口+血缘） | P10 | 无 |
| 统计检验引擎（Pearson/HAC/BH/pbo） | P11 | 仅基础指标+bootstrap/Bonferroni 雏形 |
| 独立最终测试集强制隔离 | P12 | 部分（validation 有 final test） |
| Research Memory 表（experiments/results 等） | P13 | 仅 hypotheses/kb_items |
| FTS5 文本检索 | P13 | 无 |
| Resource Manager | P14 | 无 |
| 研究调度器 | P15 | 仅年度 orchestrator |
| 市场×周期 6 组模型 | P16 | 无 |
| Shadow Mode | P17 | 无 |
| 研究 Dashboard（假设/实验/预测页） | P18 | 仅监控型 dashboard |
| Provider/研究/预测闭环测试 | P19 | 部分 |

## 部分存在（需增强）

- TimeGuard：有，但业务直读未全量收编。
- 模型对比：有 6 模型，但无市场×周期分组。
- 数据质量：有分批，但无跨源冲突/交易日缺失检查。
- Agent：有 8 个 stub + 强制门禁，无真实 LLM 工具权限控制。
- 预测冻结：有 HMAC+溯源，无密钥独立存储与环境绑定。

结论：v0.7.0 是"从研究原型到自主实验室"的大规模增量工程，应从 P1 架构与 P2 Provider 接口开始，不重写已有功能。
