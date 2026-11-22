# A股与美股量化研究系统

一套**严格点位时间隔离**、可持续进行历史模拟预测、误差分析与知识迭代的量化研究系统。**不连接真实证券账户，不执行真实交易。**

## 核心保证

预测年份 Y 时，系统可访问数据空间严格等于 `{d | d.available_time ≤ Y-1-12-31}`：

- 每次预测前，Rust `TimeGuard` 对整个快照做全量流式扫描，未来数据或时点未知数据立即中止预测。
- 知识库按 `asof_YYYY` 物理分目录，快照冻结（含文件 SHA-256）后不可被后续数据改变。
- 预测记录 SHA-256 存 SQLite，永久不可变。
- 13 项自动化防泄漏测试覆盖未来数据、未来文档、知识图谱、财报、归一化、模型权重、缓存、历史预测等全部要求。

## 技术架构

| 组件 | 技术 | 用途 |
|---|---|---|
| 时间隔离引擎 | **Rust** | 流式全量审计、文件哈希、高性能门禁 |
| ML / 统计 | **Python** (scikit-learn, LightGBM) | 基准模型、结构化预测 |
| 列式存储 | **Parquet + Apache Arrow (pyarrow)** | 分区存储、列投影 |
| 分析查询 | **DuckDB** | 本地分析 |
| 元数据 | **SQLite** | 快照、预测、任务、规则、性能 |
| 高性能分析 | **Polars** | 备选数据分析 |
| 可视化 | **Streamlit + 自包含 HTML** | 管理界面 + 静态仪表盘 |
| 部署 | **Docker Compose / systemd** | 单机部署 |

## 数据流

```
公开数据源 (fixture / FRED / SEC / AKShare)
    ↓ 五时间字段 (event/published/available/collected/revision)
raw_all.parquet → 覆盖率报告
    ↓ 按 cutoff 物理过滤
knowledge/snapshots/asof_YYYY/ (clean + knowledge + features + models + manifest)
    ↓ Rust TimeGuard 全量审计
训练基准模型 → 预测下一年 → 冻结预测 → 冻结快照
    ↓ 之后才导入下一年真实数据
误差分析 → 更新知识库/决策库 → 创建下一年快照 → 循环
```

## 快速运行

```bash
cd /root/工作区/股票
python scripts/data_pipeline.py fetch --sources fixture
PYTHONPATH=python python scripts/run.py run --start 2002 --end 2004
PYTHONPATH=python python3 -m pytest python/tests -v
python scripts/build_dashboard.py
python scripts/test_report.py
```

## v0.2.0 升级（已完成）

在 v0.1.0 基础上升级，交付 8 阶段全部成果：代码审计、时间隔离实证、真实数据源（12,716 行）、知识库升级、6 模型基准对比、性能优化（列投影 4x/去重 17x）、监控面板、**20/20 验收 + 37 项测试全过**。

- 真实数据运行：`python/scripts/run.py run --start 2002 --end 2004 --raw data/raw/raw_real.parquet`
- 完整交付报告：[docs/V02_DELIVERY_REPORT.md](docs/V02_DELIVERY_REPORT.md)
- 部署恢复：[docs/DEPLOY_RECOVERY_V02.md](docs/DEPLOY_RECOVERY_V02.md)

可视化：
- **公网仪表盘：https://a.czyz.eu.cc/**（Cloudflare Tunnel 常驻服务）
- 本机 Streamlit（8501）或直接用浏览器打开 `web/dashboard.html`

详见 [部署文档](docs/DEPLOYMENT.md) 与 [公网部署文档](docs/PUBLIC_DEPLOY.md)。
