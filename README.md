# A股与美股量化研究系统 v0.4.0

一套**严格点位时间隔离**、可持续进行历史模拟预测、误差分析与知识迭代的量化研究系统。**不连接真实证券账户，不执行真实交易。**

## 核心保证

预测年份 Y 时，统一 cutoff 为 `Y-1-12-31T23:59:59Z`（UTC），系统可访问数据空间严格等于 `{d | d.available_at ≤ cutoff}`：

- 每次预测前，Rust `TimeGuard` 对整个快照做全量流式扫描，未来数据或时点未知数据立即中止预测。
- 知识库按 `asof_YYYY` 物理分目录，快照冻结（含文件 SHA-256）后不可被后续数据改变。
- 预测记录使用 HMAC-SHA256 + 链哈希 + append-only 触发器保护，防止双端篡改。
- 自动化防泄漏测试覆盖未来数据、未来文档、知识图谱、财报、归一化、模型权重、缓存、历史预测等要求；当前 73 项测试全部通过。
- 严格模式下，宏观数据 `time_basis` 为 `estimated` / `unknown` / `imputed` 的记录禁止使用。
- 统一 `TimeGuardReader` 强制所有历史数据读取经过时间门禁；UTC 时间内核统一 `published_at / available_at / ingested_at`，旧字段仅迁移兼容。
- 可信验收 `final_acceptance_v04.py` 真实执行测试并做篡改报告、模块缺失、未来数据注入等负向测试；任一 P0 失败整体 FAIL。

## 技术架构

| 组件 | 技术 | 用途 |
|---|---|---|
| 时间隔离引擎 | **Rust** | 流式全量审计、文件哈希、UTC 时间解析、高性能门禁 |
| 统一时间内核 | **Python** (`timestamps_v04` / `timeguard_reader`) | UTC 时间戳、市场时区转换、强制 available_at ≤ cutoff |
| 可信验收 | **Python** (`final_acceptance_v04`) | 结构化判定、真实执行 pytest、负向测试、任一 P0 失败整体 FAIL |
| ML / 统计 | **Python** (scikit-learn, LightGBM, XGBoost, CatBoost, PyTorch) | 基准模型、结构化预测、多模型对比 |
| 列式存储 | **Parquet + Apache Arrow (pyarrow)** | 分区存储、列投影 |
| 分析查询 | **DuckDB** | 本地分析 |
| 元数据 | **SQLite** | 快照、预测、任务、规则、性能、六库知识、假设 |
| 高性能分析 | **Polars** | 惰性扫描、列式分析 |
| 可视化 | **Streamlit + 自包含 HTML** | 管理界面 + 静态仪表盘 |
| 部署 | **Docker Compose / systemd** | 单机部署 |
| AI 研究智能体 | **LLM（可注入）+ 确定性 stub** | 提出假设、解释事件、整理报告；不直接产生结构化预测 |

## 数据来源与验证方式

真实公开数据源（每个源独立适配器）：

- A 股：东方财富（基本信息、指数行情接口）、腾讯财经（上证指数/深证成指 2000-2004 真实日线，含成交量与前复权标记）、AKShare（退市股、指数成分股、申万行业、财报披露）。
- 美股：Yahoo Finance（指数与个股日线，16 标的）、SEC EDGAR（10-K/10-Q/8-K，真实披露时间，覆盖扩大至 20 公司）。
- 宏观：FRED / ALFRED。ALFRED 适配器获取真实历史版本（vintage），保存每个版本的原始值、发布日期与修订时间；严格模式仅使用 `real_published`，禁止 `estimated` / `unknown`。
- 覆盖率：按市场、年份、数据类别输出覆盖率与缺口，不使用合成数据填补真实缺口。

验证方式：

- Expanding Window、Rolling Window、Walk-forward、Purged Time Series Split、Embargo。
- 独立最终测试集不参与超参数选择。
- 指标：MAE、RMSE、方向准确率、Rank IC、Spearman、预测区间覆盖率、概率校准、分市场与分行业评估。
- 所有模型与简单基准比较；样本量不足时明确标记统计证据不足。

## 数据流

```
公开真实数据源 (Eastmoney / Tencent / AKShare / Yahoo / SEC / ALFRED)
    ↓ 五时间字段 (event/published/available/ingested/revision，UTC)
data/raw/raw_v03.parquet → 覆盖率报告
    ↓ TimeGuardReader 按 available_at ≤ cutoff 过滤
knowledge/snapshots/asof_YYYY/ (clean + knowledge + features + models + manifest)
    ↓ Rust TimeGuard 全量审计 + manifest SHA-256 校验
训练模型 → 多期限预测 (1d/5d/20d/1m/3m/6m/1y + 年度) → 冻结预测（HMAC 链）
    ↓ 之后才导入下一年真实数据
误差分析 → 更新知识库/决策库 → 创建下一年快照 → 循环
```

## 快速运行

```bash
cd /root/工作区/股票
PYTHONPATH=python python3 python/scripts/fetch_v03.py
PYTHONPATH=python python3 python/scripts/run.py run --start 2002 --end 2004 --raw data/raw/raw_real.parquet
PYTHONPATH=python python3 python/scripts/validation_report.py
PYTHONPATH=python python3 python/scripts/perf_v03.py
PYTHONPATH=python python3 -m pytest python/tests -q
PYTHONPATH=python python3 python/scripts/test_report_v03.py
PYTHONPATH=python python3 python/scripts/final_acceptance_v04.py
PYTHONPATH=python python3 python/scripts/fetch_v04_indices.py
PYTHONPATH=python python3 -m coverage run --source=python/quant -m pytest python/tests -q
PYTHONPATH=python python3 -m coverage report
python3 python/scripts/build_dashboard.py
```

## v0.4.0 P0 重构（已完成）

在 v0.3.0 基础上增量升级，未改动历史报告、data/ 敏感资产与 Git 历史：

- 可信验收：结构化输出 `{input, execution, result, failure_reason}`；真实执行 pytest；篡改报告、模块缺失、未来数据注入等负向测试；任一 P0 失败整体 FAIL。
- 统一时间内核：UTC 时间戳与字段迁移（forward/rollback 仅在临时副本验证），cutoff=`Y-1-12-31T23:59:59Z`，市场时区转换，`TimeGuardReader` 强制门禁，Rust/Python 一致测试。
- ALFRED 宏观版本：补 `vintage_version` 与 `first_release_date`（估计值明确标记），准入仍用真实 vintage。
- 性能优化：东财 kline 限流后改用腾讯财经取得上证/深证 2000-2004 各 1200 根真实日线（共 2400 行），适配器含分批、退避、原始响应 SHA-256、异常价格检查。
- 验收与覆盖率：v0.4 验收 8/8 PASS（overall=PASS），73 项测试全部通过，coverage 已纳入验收（报告 32%）。

完整交付报告：[docs/V0.4.0_DELIVERY_REPORT.md](docs/V0.4.0_DELIVERY_REPORT.md)；审计报告：[docs/V04_AUDIT_REPORT.md](docs/V04_AUDIT_REPORT.md)

### v0.3.0 升级（已完成）

在 v0.2.0 基础上增量升级：

- P0：数据覆盖扩展（真实数据 44,021 行）、ALFRED 宏观历史版本（22,598 条）、7 种预测期限样本、5 种严格验证切分。
- P1：六库知识系统、8 个 AI 研究智能体、研究假设状态机（失败假设保留）、密钥安全轮换与恢复/篡改测试。
- P2：性能优化（列投影加速、DuckDB 聚合加速），报告含运行时间、峰值 RSS、磁盘占用与结果一致性。
- 最终验收：14/14 PASS，59 项测试全部通过。

完整交付报告：[docs/V03_DELIVERY_REPORT.md](docs/V03_DELIVERY_REPORT.md)

## 已知缺口（未用合成数据补齐）

1. A 股上证指数/深证成指 2000-2004 真实日线已由腾讯财经补齐（2400 行）；沪深300/中证500/中证1000 在该区间无数据（真实历史：发布较晚），A 股个股日线因东方财富 kline 限流仍 BLOCKED，未用合成数据补齐。
2. 历史指数成分股：仅获得当前名单与纳入日期。
3. 历史行业分类版本：仅获得当前申万分类。
4. A 股 2000-2004 财报披露的完整历史版：AKShare 仅提供特定期数。
5. FRED 非 vintage 序列的估计发布时间数据：严格模式禁用。

## 参考文档与可视化

- 完整交付报告：[docs/V0.4.0_DELIVERY_REPORT.md](docs/V0.4.0_DELIVERY_REPORT.md)（v0.4.0）、[docs/V03_DELIVERY_REPORT.md](docs/V03_DELIVERY_REPORT.md)（v0.3.0）
- 部署与恢复：[docs/DEPLOY_RECOVERY_V02.md](docs/DEPLOY_RECOVERY_V02.md)、[部署文档](docs/DEPLOYMENT.md)、[公网部署文档](docs/PUBLIC_DEPLOY.md)
- 审计报告：[docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md)
- 公网仪表盘：https://a.czyz.eu.cc/（Cloudflare Tunnel 常驻服务）
- 本机 Streamlit（8501）或直接用浏览器打开 `web/dashboard.html`
