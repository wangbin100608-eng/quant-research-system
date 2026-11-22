# 部署与运行文档

## 1. 项目结构

```
/root/工作区/股票/
├── config/                  配置（config.yaml / sources.yaml）
├── rust_core/               Rust 时间隔离引擎（timeguard CLI）
├── python/
│   ├── quant/               核心库（契约、存储、时间隔离、KB、特征、模型、评估、编排）
│   ├── adapters → quant/adapters
│   ├── dashboard/app.py     Streamlit 管理界面
│   ├── scripts/             数据管道 / 年度循环 / 仪表盘 / 测试报告
│   └── tests/test_leakage.py 防泄漏测试（13 项）
├── web/dashboard.html       静态可视化仪表盘（自包含，无外部依赖）
├── docker/                  Dockerfile + docker-compose.yml
├── deploy/                  systemd unit + logrotate
└── data/                    数据、快照、模型、预测、报告、日志
```

## 2. 快速开始（本机）

```bash
cd /root/工作区/股票
# 依赖已通过 pip --break-system-packages 安装；也可使用 requirements.txt
python scripts/data_pipeline.py fetch --sources fixture
PYTHONPATH=python python scripts/run.py run --start 2002 --end 2004
PYTHONPATH=python python3 -m pytest python/tests -v
python scripts/build_dashboard.py
python scripts/test_report.py
# 可视化
python3 -m streamlit run python/dashboard/app.py --server.port 8501
# 或打开静态仪表盘 web/dashboard.html
```

## 3. Docker 部署（推荐）

```bash
cd /root/工作区/股票
docker compose -f docker/docker-compose.yml up -d --build
```

- `quant-init`：一次性初始化（抓取数据 → 2002~2004 年度循环 → 13 项测试 → 仪表盘）
- `quant-web`：Streamlit 管理界面，映射 **8501**
- `quant-static`：静态仪表盘（Nginx），映射 **8080**
- 数据卷 `quant_data` 持久化 `data/` 全部内容

验证：

```bash
docker compose -f docker/docker-compose.yml ps
curl http://localhost:8501
curl http://localhost:8080
docker logs quant-init | tail -20
```

## 4. systemd 部署（无 Docker 时）

```bash
sudo cp deploy/quant-web.service deploy/quant-loop.service deploy/quant-loop.timer /etc/systemd/system/
sudo cp deploy/logrotate.conf /etc/logrotate.d/quant-research
sudo systemctl daemon-reload
sudo systemctl enable --now quant-web.service
sudo systemctl enable --now quant-loop.timer   # 每天 03:30 自动研究循环 + 备份
```

## 5. 备份与告警

```bash
bash scripts/backup.sh              # 备份到 data/backups/<时间戳>，保留最近 10 份
ALERT_WEBHOOK=<url> bash scripts/alert.sh TASK MSG
```

日志轮转由 Python `RotatingFileHandler`（10MB × 5）与 logrotate 双重保障。

## 6. 时间隔离保证（最重要）

- **物理隔离**：`data/knowledge/snapshots/asof_YYYY/` 每个目录只含截止日期前的数据；预测进程只加载对应快照。
- **Rust 全量扫描**：每次预测前后运行 `timeguard audit`，流式扫描全部 `.timeindex`，发现 `available_time > cutoff` 或 `unknown_availability` 立即中止。
- **不可变冻结**：预测前快照写入 manifest（文件 SHA-256 + 条目数 + 时间范围 + sealed），预测后冻结；历史快照无法被后续数据导入改变。
- **不可变预测**：预测记录 SHA-256 存 SQLite，`verify_all()` 检测任何篡改。
- **模型门禁**：禁止加载 `trained_to > cutoff` 或 `built_year > prediction_year` 的权重。

验证命令：

```bash
# Rust 直接审计
rust_core/target/release/timeguard audit \
  --snapshot data/knowledge/snapshots/asof_2001 \
  --cutoff 2001-12-31 --json
# 预期: {"ok": true, ... "violations": []}

# 13 项防泄漏测试
PYTHONPATH=python python3 -m pytest python/tests -v
```

## 7. 数据源说明

- **fixture**：明确标注 `synthetic: true` 的合成数据，仅用于端到端验收，不冒充真实历史行情。
- **FRED / SEC EDGAR / AKShare**：适配器已实现，按真实披露时间/发布日期作为 `available_time`；网络不可用时明确记录缺口，**不伪造数据**。
- 覆盖率报告 `data/reports/coverage_report.json` 在训练前生成，标注缺失范围。

## 8. 验收结果

执行 `python scripts/test_report.py` 生成 `data/reports/test_report.json`，含 16 项验收逐条结果与性能数据。
