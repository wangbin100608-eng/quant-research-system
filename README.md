# A股与美股量化研究系统

> 当前交付：v0.6.0 可信量化研究系统（P0-P6：PIT 股票池、列投影门禁、强制溯源、分批数据质量、统计可信度）
> 测试结果：**121 passed** ｜ 远端分支：`main`（v0.6.0 开发中，分支 `feat/v060-p0-pit`）

一套**严格点位时间隔离**、可持续进行历史模拟预测、误差分析与知识迭代的量化研究系统。**不连接真实证券账户，不执行真实交易。**

## 核心保证

预测年份 Y 时，统一 cutoff 为 `Y-1-12-31T23:59:59Z`（UTC），系统可访问数据空间严格等于 `{d | d.available_at ≤ cutoff}`：

- 每次预测前，Rust `TimeGuard` 对整个快照做全量流式扫描，未来数据或时点未知数据立即中止预测。
- 知识库按 `asof_YYYY` 物理分目录，快照冻结（含文件 SHA-256）后不可被后续数据改变。
- 预测记录使用 HMAC-SHA256 + 链哈希 + append-only 触发器保护，防止双端篡改；v0.5 补充完整溯源（prediction_id、数据快照、模型版本、特征版本、代码 Commit、训练数据最大 available_at、预测 cutoff、frozen_at、修正链）。
- 自动化防泄漏测试覆盖未来数据、未来文档、知识图谱、财报、归一化、模型权重、缓存、历史预测等要求；当前 **121 项测试全部通过**。
- 严格模式下，宏观数据 `time_basis` 为 `estimated` / `unknown` / `imputed` 的记录禁止使用。
- 统一 `TimeGuardReader` 强制所有历史数据读取经过时间门禁；UTC 时间内核统一 `published_at / available_at / ingested_at`，旧字段仅迁移兼容。
- `query_sql` 强制显式 cutoff，结果必须含 available_at，无时间字段严格拒绝，防子查询绕过；`stream_parquet` 分批流式读取，结果须与全量读取一致。
- 可信验收 `final_acceptance_v05.py` 与 v0.4.1 修复组件真实执行 pytest 并做模块删除、报告篡改、未来数据注入等负向测试；任一 P0 失败整体 FAIL，退出码非零。

## 技术架构

| 组件 | 技术 | 用途 |
|---|---|---|
| 时间隔离引擎 | **Rust** | 流式全量审计、文件哈希、UTC 时间解析、高性能门禁 |
| 统一时间内核 | **Python** (`timestamps_v04` / `timeguard_reader`) | UTC 时间戳、市场时区转换、强制 available_at ≤ cutoff、query_sql 时间门禁、分批流式读取 |
| 可信验收 | **Python** (`final_acceptance_v05`、`module_delete_v041`、`report_verifier_v041`、`strict_kb_v041`、`test_manifest_v041`) | 真实负向闭环、报告验证、六库严格检查、版本化测试清单、任一 P0 失败整体 FAIL 且非零退出 |
| 预测冻结 | **Python** (`prediction_freeze_v05`) | 完整溯源、HMAC 链、append-only、修正版本链 |
| ML / 统计 | **Python** (scikit-learn, LightGBM, XGBoost, CatBoost, PyTorch) | 基准模型、结构化预测、多模型对比 |
| 列式存储 | **Parquet + Apache Arrow (pyarrow)** | 分区存储、列投影、分批读取 |
| 分析查询 | **DuckDB** | 列式数据查询、跨年份分析 |
| 元数据 | **SQLite** | 快照、预测、任务、规则、性能、六库知识、假设、溯源与审计 |
| 高性能分析 | **Polars** | 惰性扫描、列式分析 |
| 可视化 | **Streamlit + 自包含 HTML** | 管理界面 + 静态仪表盘 |
| 部署 | **Docker Compose / systemd** | 单机部署 |
| AI 研究智能体 | **LLM（可注入）+ 确定性 stub** | 提出假设、解释事件、整理报告；不直接产生结构化预测 |

## 数据来源与验证方式

真实公开数据源（每个源独立适配器）：

- A 股：东方财富（基本信息、指数行情接口）、腾讯财经（上证指数/深证成指 2000-2004 真实日线，含成交量与前复权标记）、AKShare（退市股、指数成分股、申万行业、财报披露）。
- 美股：Yahoo Finance（指数与个股日线，16 标的）、SEC EDGAR（10-K/10-Q/8-K，真实披露时间，覆盖扩大至 20 公司）。
- 宏观：FRED / ALFRED。ALFRED 适配器获取真实历史版本（vintage），保存每个版本的原始值、发布日期、修订时间、`vintage_version` 与 `first_release_date`（估计值明确标记）；严格模式仅使用 `real_published`，禁止 `estimated` / `unknown`。
- 覆盖率：按市场、年份、数据类别输出覆盖率与缺口，不使用合成数据填补真实缺口。

验证方式：

- Expanding Window、Rolling Window、Walk-forward、Purged Time Series Split、Embargo。
- 独立最终测试集不参与超参数选择。
- 指标：MAE、RMSE、方向准确率、Rank IC、Spearman、预测区间覆盖率、概率校准、分市场与分行业评估。
- 所有模型与简单基准比较；样本量不足时明确标记统计证据不足。
- 负向测试：模块删除→FAIL→恢复→PASS、报告四类篡改拒绝、六库空库/缺库检测、Parquet/DuckDB/SQLite/KB 未来数据注入、SQL 时间门禁、分批读取一致性。

## 数据流

```
公开真实数据源 (Eastmoney / Tencent / AKShare / Yahoo / SEC / ALFRED)
    ↓ 五时间字段 (event/published/available/ingested/revision，UTC)
data/raw/raw_v03.parquet → 覆盖率报告
    ↓ TimeGuardReader 按 available_at ≤ cutoff 过滤（全量/分批/SQL 三路径）
knowledge/snapshots/asof_YYYY/ (clean + knowledge + features + models + manifest)
    ↓ Rust TimeGuard 全量审计 + manifest SHA-256 校验
训练模型 → 多期限预测 (1d/5d/20d/1m/3m/6m/1y + 年度) → 冻结预测（HMAC 链 + 完整溯源）
    ↓ 之后才导入下一年真实数据
误差分析 → 更新知识库/决策库 → 创建下一年快照 → 循环
```

## 快速运行

```bash
cd /root/工作区/股票
python3 python/scripts/fetch_v03.py
PYTHONPATH=python python3 python/scripts/fetch_v04_indices.py
PYTHONPATH=python python3 python/scripts/run.py run --start 2002 --end 2004 --raw data/raw/raw_real.parquet
PYTHONPATH=python python3 python/scripts/validation_report.py
PYTHONPATH=python python3 python/scripts/perf_v03.py
PYTHONPATH=python python3 python/scripts/stream_benchmark_v05.py
PYTHONPATH=python python3 -m pytest python/tests -q
PYTHONPATH=python python3 python/scripts/final_acceptance_v05.py
PYTHONPATH=python python3 python/scripts/prediction_freeze_v05.py verify
PYTHONPATH=python python3 -m coverage run --source=python/quant -m pytest python/tests -q
PYTHONPATH=python python3 -m coverage report
python3 python/scripts/build_dashboard.py
```

## v0.6.0 开发（当前分支 feat/v060-p0-pit）

P0 股票池 PIT/列投影门禁/流式审计/验收器修复；P1 Agent/Embedding/模型强制时间门禁与预测冻结强化；P2/P3 数据质量 Arrow 分批与数据现状报告；P4 100万/500万行 RSS 性能基准；P5 Bootstrap CI/Bonferroni 校正/证据等级；P6 交付报告。121 测试通过、验收 6/6 PASS、Rust selftest PASS。
报告：[docs/V0.6.0_DELIVERY_REPORT.md](docs/V0.6.0_DELIVERY_REPORT.md)

## 版本交付记录

### v0.4.1 修复（当前增量，HEAD `4ffef79`）

- P0：真实模块删除测试（临时项目副本 + 独立进程 + 多模块）、独立报告验证器（结构/版本/commit/时间/数量/PASS-FAIL/哈希）、六库严格检查（实名/真实记录/时间范围/空库与缺库负向）、版本化测试清单（唯一 ID、manifest 哈希、删除/跳过/失败/错误/xfail）。
- P1：`TimeGuardReader.query_sql` 强制 cutoff 与 available_at 检查、防子查询绕过；`stream_parquet` 分批读取一致性；DuckDB 未来数据注入测试。
- 最终验收 9 项 PASS，全量 **100 passed**，覆盖率 **39%**。
- 报告：[docs/V0.4.1_DELIVERY_REPORT.md](docs/V0.4.1_DELIVERY_REPORT.md)

### v0.5.0 升级（已完成）

全链路防未来数据泄漏与高性能数据引擎：可信验收 v05（真实负向闭环/非零退出码）、预测冻结完整溯源（HMAC）、Embedding/模型/Agent 溯源审计、`TimeGuardReader` 分批流式读取（100 万行峰值 84.6MB→10.46MB）、历史股票池（保留退市股、防幸存者偏差）与数据质量系统。v0.5 验收 4/4 PASS。
报告：[docs/V0.5.0_DELIVERY_REPORT.md](docs/V0.5.0_DELIVERY_REPORT.md)

### v0.4.0 P0 重构（已完成）

可信验收 v04、UTC 时间内核、ALFRED 宏观版本字段、腾讯真实指数日线、coverage 纳入验收；验收 8/8 PASS，73 项测试通过。

## 已知缺口（未用合成数据补齐）

1. A 股上证指数/深证成指 2000-2004 真实日线已由腾讯财经补齐（2400 行）；沪深300/中证500/中证1000 在该区间无数据（真实历史：发布较晚），A 股个股日线因东方财富 kline 限流仍 **BLOCKED**，未用合成数据补齐。
2. 历史指数成分股：仅获得当前名单与纳入日期；历史行业分类版本：仅获得当前申万分类。
3. A 股 2000-2004 财报披露的完整历史版：AKShare 仅提供特定期数。
4. FRED 非 vintage 序列的估计发布时间数据：严格模式禁用。
5. 覆盖率未达标：`timeguard_reader` 81%、`timeguard` 65%、`storage` 67%、`kb.py` 51%；分支覆盖率、Docker 重新部署、CI 日志密钥检查、1000 万/5000 万行压力测试为 **NOT_TESTED**。

## 参考文档与可视化

- 最新交付报告：[docs/V0.4.1_DELIVERY_REPORT.md](docs/V0.4.1_DELIVERY_REPORT.md)
- 版本交付报告：[docs/V0.5.0_DELIVERY_REPORT.md](docs/V0.5.0_DELIVERY_REPORT.md)、[docs/V0.4.0_DELIVERY_REPORT.md](docs/V0.4.0_DELIVERY_REPORT.md)
- 阶段一审计：[docs/V04_AUDIT_REPORT.md](docs/V04_AUDIT_REPORT.md)、[docs/V05_STAGE1_AUDIT.md](docs/V05_STAGE1_AUDIT.md)
- 数据覆盖：[docs/REAL_DATA_REPORT.md](docs/REAL_DATA_REPORT.md)；模型对比：[docs/MODEL_COMPARISON_REPORT.md](docs/MODEL_COMPARISON_REPORT.md)；性能：[docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md)
- 测试说明：[docs/TESTING.md](docs/TESTING.md)；变更记录：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 部署与恢复：[docs/DEPLOY_RECOVERY_V02.md](docs/DEPLOY_RECOVERY_V02.md)、[部署文档](docs/DEPLOYMENT.md)、[公网部署文档](docs/PUBLIC_DEPLOY.md)
- 公网仪表盘：https://a.czyz.eu.cc/（Cloudflare Tunnel 常驻服务）
- 本机 Streamlit（8501）或直接用浏览器打开 `web/dashboard.html`
