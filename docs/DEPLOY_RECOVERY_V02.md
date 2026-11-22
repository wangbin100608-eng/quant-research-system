# v0.2.0 部署、备份与恢复说明

## 1. 环境

- Python 3.12（依赖见 `python/requirements.txt`；新增 xgboost/catboost/torch cpu）
- Rust（仅编译期，用 Docker `rust:1.83-alpine` 编译 timeguard，产出 `rust_core/target/release/timeguard`）
- Docker / Docker Compose（可选，推荐）
- 内存：单机 1GB 即可运行（真实数据抓取峰值约 31MB，年度循环约 75MB）

## 2. 首次部署（本机）

```bash
cd /root/工作区/股票
# 1) 真实数据（写 data/raw/raw_real.parquet，不碰 fixture）
PYTHONPATH=python python3 python/scripts/fetch_real.py
# 2) 完整年度循环（真实数据，2002-2004）
PYTHONPATH=python python3 python/scripts/run.py run --start 2002 --end 2004 --raw data/raw/raw_real.parquet
# 3) 全部测试（37 项）
PYTHONPATH=python python3 -m pytest python/tests -q
# 4) 基准对比 / 性能 / 最终验收 / 综合报告
PYTHONPATH=python python3 python/scripts/model_benchmark.py
PYTHONPATH=python python3 python/scripts/perf_benchmark.py
PYTHONPATH=python python3 python/scripts/final_acceptance.py
PYTHONPATH=python python3 python/scripts/test_report.py
# 5) 仪表盘（静态 + Streamlit）
PYTHONPATH=python python3 python/scripts/build_dashboard.py
python3 -m streamlit run python/dashboard/app.py --server.port 8501
```

## 3. Docker 部署

```bash
docker compose -f docker/docker-compose.yml up -d --build
# quant-init 需改用真实数据命令（见 docker-compose.yml 的 command）
```

## 4. 备份

```bash
bash scripts/backup.sh                    # data/backups/<时间戳>/，保留最近 10 份
# 关键对象：
#   data/meta.db                  元数据库（快照/预测/链/任务/性能）
#   data/.seal_key                HMAC 签名密钥（chmod 600，务必单独备份！）
#   data/knowledge/snapshots/     时间隔离快照（含 manifest.json）
#   data/predictions/             冻结预测（HMAC 链）
#   data/raw/raw_real.parquet     真实原始数据
```

**警告**：`data/.seal_key` 丢失将导致已冻结预测无法验证签名；泄露则攻击者可重签。请与数据分开、加密保存。设置 `QUANT_SEAL_KEY` 环境变量可覆盖文件密钥。

## 5. 恢复

### 5.1 快照恢复（阶段4 新增 API）

```python
from quant.knowledge import KnowledgeBase
kb = KnowledgeBase()
result = kb.restore_snapshot_from_manifest(2001, "data/backups/<时间戳>")
# 从备份复制 asof_2001 后用 manifest 重新验证（sealed 快照拒绝覆盖）
```

### 5.2 完整恢复

1. 停服务，备份当前 `data/`（即使损坏也保留现场）
2. 从备份复制：`meta.db`、`.seal_key`、`snapshots/`、`predictions/`、`raw_real.parquet`
3. 校验：
```bash
PYTHONPATH=python python3 python/scripts/final_acceptance.py   # PASS 必须为 true
PYTHONPATH=python python3 python/scripts/run.py verify          # HMAC 链完整性
./rust_core/target/release/timeguard selftest
```
4. 重启服务

### 5.3 重新验证任意快照（不改文件）

```python
from quant.knowledge import KnowledgeBase
from quant.contracts import PredictionSpec
kb = KnowledgeBase()
spec = PredictionSpec.for_year(2003, 2000, "asof_2002", "audit", "feature_v1")
print(kb.verify_snapshot(2002, spec, require_sealed=True))
# {sealed: True, hashes_match: True, future_rows: 0, ok: True}
```

## 6. append-only 保证

`predictions` 表有 SQLite 触发器禁止 UPDATE/DELETE；签名链存 `prediction_chain` 表。任何绕过触发器的直接文件编辑若无 `.seal_key` 都无法生成合法 HMAC。`verify_all()` 同时校验：
- 每条 HMAC 签名与链连续性（检测删行/插队/重排）
- DB 中 `spec_json/result_json` 与文件内容一致（消除双份真相源）
- `pred_id` 对应文件存在

## 7. 公网页面

静态仪表盘经 Cloudflare Tunnel 发布至 https://a.czyz.eu.cc/（详见 PUBLIC_DEPLOY.md）。
更新内容后重新生成即可（Nginx 挂载目录即时生效）：
```bash
PYTHONPATH=python python3 python/scripts/build_dashboard.py
```
