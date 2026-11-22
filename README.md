# A股与美股量化研究系统 v0.7.0

> 当前交付：v0.7.0 Autonomous Quantitative Research Lab（P0-P21 执行闭环，含受限项）｜ 远端分支：`main`
> 测试结果：**139 passed** ｜ 验收：**6/6 PASS** ｜ Rust selftest：**PASS**

一套**严格点位时间隔离**、可持续进行历史模拟预测、误差分析与知识迭代的量化研究系统。**不连接真实证券账户，不执行真实交易。**

## 核心保证

预测年份 Y 时，统一 cutoff 为 `Y-1-12-31T23:59:59Z`（UTC），系统可访问数据空间严格等于 `{d | d.available_at ≤ cutoff}`：

- 每次预测前，Rust `TimeGuard` 对整个快照做全量流式扫描，未来数据或时点未知数据立即中止预测。
- 知识库按 `asof_YYYY` 物理分目录，快照冻结（含文件 SHA-256）后不可被后续数据改变。
- 预测记录使用 HMAC-SHA256 + 链哈希 + append-only 触发器保护；包含 prediction_id、数据快照、模型版本、特征版本、代码 Commit、训练数据最大 available_at、预测 cutoff、frozen_at、输入/输出 SHA-256、密钥版本与修正链。
- 自动化防泄漏测试覆盖未来数据、未来文档、知识图谱、财报、归一化、模型权重、缓存、历史预测等要求；当前 **139 项测试全部通过**。
- 严格模式下，宏观数据 `time_basis` 为 `estimated` / `unknown` / `imputed` 的记录禁止使用。
- 统一 `TimeGuardReader` 强制所有历史数据读取经过时间门禁；无论列投影如何选择，内部自动读取 available_at 过滤后再投影；`query_sql` 强制显式 cutoff、无时间字段严格拒绝、防子查询绕过；`stream_parquet` 分批流式读取并逐批审计，结果与全量一致。
- 历史股票池按年度 cutoff 构建：退市记录双时间校验（信息发布时间 + 真实退市日期），未来才公布的退市信息不能反向进入早期快照；指数成分/行业当前名单标 BLOCKED，不冒充历史版本。
- Agent / Embedding / 模型均带强制时间门禁：工具调用必须提供 snapshot_id 与 cutoff，越权阻断并持久化；Embedding 追溯到原始文档哈希与 cutoff；模型选择不得使用未来测试集结果。
- 可信验收真实执行 pytest 并做模块删除、报告篡改、六库空库/缺库、未来数据注入、SQL 门禁、分批一致性等负向测试；任一 P0 失败整体 FAIL，退出码非零。

## 技术架构

| 组件 | 技术 | 用途 |
|---|---|---|
| 时间隔离引擎 | **Rust** | 流式全量审计、文件哈希、UTC 时间解析、高性能门禁 |
| 统一时间内核 | **Python** (`timestamps_v04` / `timeguard_reader`) | UTC 时间戳、市场时区转换、强制 available_at ≤ cutoff、列投影时间门禁、query_sql、分批流式读取 |
| 历史股票池与质量 | **Python** (`data_quality_v05`) | PIT 股票池、Arrow 分批数据质量、多维评分、幸存者偏差防护 |
| 强制时间门禁 | **Python** (`enforcement_v060`) | Agent 工具调用/文档检索越权阻断、Embedding 与模型选择防泄漏 |
| 可信验收 | **Python** (`final_acceptance_v05`、`module_delete_v041`、`report_verifier_v041`、`strict_kb_v041`、`test_manifest_v041`) | 真实负向闭环、报告验证、六库严格检查、版本化测试清单、任一 P0 失败整体 FAIL 且非零退出 |
| 预测冻结 | **Python** (`prediction_freeze_v05`) | 完整溯源、HMAC 链、append-only、输入/输出哈希、密钥版本、修正链 |
| ML / 统计 | **Python** (scikit-learn, LightGBM, XGBoost, CatBoost, PyTorch) | 基准模型、结构化预测、多模型对比 |
| 统计可信度 | **Python** (`stats_v060`) | Bootstrap 置信区间、Bonferroni 多重检验校正、证据等级、Mann-Whitney |
| 列式存储 | **Parquet + Apache Arrow (pyarrow)** | 分区存储、列投影、分批读取 |
| 分析查询 | **DuckDB** | 列式数据查询、跨年份分析 |
| 元数据 | **SQLite** | 快照、预测、任务、规则、性能、六库知识、假设、溯源与审计 |
| 高性能分析 | **Polars** | 惰性扫描、列式分析 |
| 可视化 | **Streamlit + 自包含 HTML** | 管理界面 + 静态仪表盘 |
| 部署 | **Docker Compose / systemd** | 单机部署 |

## 数据来源与验证方式

真实公开数据源（每个源独立适配器）：

- A 股：东方财富（基本信息、指数行情接口）、腾讯财经（上证指数/深证成指 2000-2004 真实日线，含成交量与前复权标记）、AKShare（退市股、指数成分股、申万行业、财报披露）。
- 美股：Yahoo Finance（指数与个股日线，16 标的）、SEC EDGAR（10-K/10-Q/8-K，真实披露时间，覆盖扩大至 20 公司）。
- 宏观：FRED / ALFRED。ALFRED 适配器获取真实历史版本（vintage），保存每个版本的原始值、发布日期、修订时间、`vintage_version` 与 `first_release_date`（估计值明确标记）；严格模式仅使用 `real_published`，禁止 `estimated` / `unknown`。
- 覆盖率与真实性：按市场、年份、数据类别输出覆盖率与缺口，区分真实、估算与合成数据，不使用合成数据补齐真实缺口。

验证方式：

- Expanding Window、Rolling Window、Walk-forward、Purged Time Series Split、Embargo。
- 独立最终测试集不参与超参数选择；模型选择依据不得引用最终测试集。
- 指标：MAE、RMSE、方向准确率、Rank IC、Spearman、预测区间覆盖率、概率校准、Bootstrap 置信区间、统计证据等级。
- 所有模型与简单基准比较；样本量不足时明确标记统计证据不足。
- 负向测试：模块删除→FAIL→恢复→PASS、报告四类篡改拒绝、六库空库/缺库检测、Parquet/DuckDB/SQLite/KB 未来数据注入、SQL 时间门禁、列投影绕过测试、分批读取一致性、退市信息注入与快照哈希复现。

## 数据流

```
公开真实数据源 (Eastmoney / Tencent / AKShare / Yahoo / SEC / ALFRED)
    ↓ 五时间字段 (event/published/available/ingested/revision，UTC)
data/raw/raw_v03.parquet → 覆盖率报告 → 数据现状报告
    ↓ TimeGuardReader 按 available_at ≤ cutoff 过滤（全量/列投影/分批/SQL 四路径）
knowledge/snapshots/asof_YYYY/ (clean + knowledge + features + models + manifest)
    ↓ Rust TimeGuard 全量审计 + manifest SHA-256 校验
训练模型（Walk-forward / Purged / Embargo，防止未来信息泄漏）
    ↓ 多期限预测 (1d/5d/20d/1m/3m/6m/1y + 年度) → 冻结预测（HMAC 链 + 完整溯源）
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

## 当前交付：v0.7.0（P0-P21）

- P0：股票池 PIT 隔离、列投影强制时间门禁、流式分批审计、验收器修复。
- P1：Agent / Embedding / 模型强制时间门禁，预测冻结必需字段与双哈希。
- P2/P3：真实数据现状报告、Arrow 分批数据质量与多维评分。
- P4：100 万 / 500 万行 RSS 性能基准（峰值 RSS 208MB，增量 58.68MB / 0MB）。
- P5：Bootstrap 置信区间、Bonferroni 多重检验校正、统计证据等级。
- P6：交付报告与文档一致性。
- P0-P21 逐阶段执行结果总览：[reports/v0.7.0/P21_EXECUTION_SUMMARY.md](reports/v0.7.0/P21_EXECUTION_SUMMARY.md)
- 验证：**139 项测试通过**，验收 **6/6 PASS**，Rust selftest **PASS**。
- 收尾：P11 高级统计（HAC/Block Bootstrap/BH/Holm/CSCV/DSR）、P13 Research Memory 9表+血缘、P16 六组基线（US PASS/CN_A BLOCKED）、P20 密钥扫描已补齐
- 受限：P2/P3/P5/P16/P20/P21 仍含部分项，Tushare/Alpaca 等外部数据源 BLOCKED，高级统计与完整模型基线未全部完成——详见总览，未写成完成。

v0.6.0 交付报告：[docs/V0.6.0_DELIVERY_REPORT.md](docs/V0.6.0_DELIVERY_REPORT.md)

## 已知缺口（未用合成数据补齐）

1. A 股上证指数/深证成指 2000-2004 真实日线已由腾讯财经补齐（2400 行）；沪深300/中证500/中证1000 在该区间无数据（真实历史：发布较晚），A 股个股日线因东方财富 kline 限流仍 **BLOCKED**。
2. 历史指数成分股与历史行业分类版本未完整获取，当前名单仅作参考并标 **BLOCKED**。
3. A 股 2000-2004 财报披露的完整历史版：AKShare 仅提供特定期数。
4. FRED 非 vintage 序列的估计发布时间数据：严格模式禁用。
5. **NOT_TESTED**：1000 万 / 5000 万行压力测试（本机 1GB 内存限制）、Docker 重新部署、CI 日志密钥扫描、分支覆盖率统计。

## 参考文档与可视化

- v0.7.0 执行总览：[reports/v0.7.0/P21_EXECUTION_SUMMARY.md](reports/v0.7.0/P21_EXECUTION_SUMMARY.md)；P0 审计：[reports/v0.7.0/current_architecture.md](reports/v0.7.0/current_architecture.md)
- 数据现状：[data/reports/data_status_v060.json](data/reports/data_status_v060.json)；大规模性能：[data/reports/scale_performance_v060.json](data/reports/scale_performance_v060.json)
- 数据覆盖：[docs/REAL_DATA_REPORT.md](docs/REAL_DATA_REPORT.md)；模型对比：[docs/MODEL_COMPARISON_REPORT.md](docs/MODEL_COMPARISON_REPORT.md)；性能：[docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md)
- 测试说明：[docs/TESTING.md](docs/TESTING.md)；变更记录：[docs/CHANGELOG.md](docs/CHANGELOG.md)
- 部署与恢复：[docs/DEPLOY_RECOVERY_V02.md](docs/DEPLOY_RECOVERY_V02.md)、[部署文档](docs/DEPLOYMENT.md)、[公网部署文档](docs/PUBLIC_DEPLOY.md)
- 公网仪表盘：https://a.czyz.eu.cc/（Cloudflare Tunnel 常驻服务）
- 本机 Streamlit（8501）或直接用浏览器打开 `web/dashboard.html`
