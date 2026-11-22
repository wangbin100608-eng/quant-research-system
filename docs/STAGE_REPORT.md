# 分阶段执行报告

| 阶段 | 内容 | 状态 | 关键证据 |
|---|---|---|---|
| 0 | 环境准备 | ✅ | Python 3.12、Docker 29、Rust 工具链（容器编译）、目录骨架 |
| 1 | 契约层与存储 | ✅ | `contracts.py` 五时间字段；SQLite 7 表；Parquet/DuckDB 存储 |
| 2 | 时间隔离引擎 | ✅ | `TimeTravelEngine` + Rust `timeguard`（520KB Release 二进制） |
| 3 | 数据适配器 | ✅ | fixture/FRED/SEC/AKShare；13,640 行；覆盖率报告标注 synthetic |
| 4 | 知识库快照 | ✅ | asof_2001/2002/2003/2004，manifest 含 SHA-256，前三个已冻结 |
| 5 | 防泄漏测试 | ✅ | 16 项测试（8 类强制检查 + 语义/语义黄金样例 + Rust 审计） |
| 6 | 模型与评估 | ✅ | 基准均值模型 + LightGBM（可选降级）；不可变预测 SHA-256 |
| 7 | 年度循环 | ✅ | 2002→2003→2004 各 17 步，checkpoint 可续跑 |
| 8 | 可视化 | ✅ | Streamlit 8 标签页 + 自包含 HTML 仪表盘（SVG 图表） |
| 9 | Docker 部署 | ✅ | 多阶段构建；init/web/static 三容器；systemd + 备份 + 告警 + logrotate |
| 10 | 端到端验收 | ✅ | 16/16 验收通过；容器内 16 测试通过；8501/8080 HTTP 200 |

## 关键运行指标

- fixture 数据抓取：13,640 行，3.2s，内存峰值 39.23 MB
- 年度循环：2002=9.52s / 2003=0.87s / 2004=1.39s（首次含模型训练）
- 快照剔除未来数据：预测 2002 时从 13,640 行剔除 8,206 行（available_time > 2001-12-31）
- Rust 审计：asof_2001 扫描 5,434 行零违规；asof_2002 扫描 8,164 行零违规
- 预测完整性：3 条记录，篡改检测 0 异常

## 最重要的验收证明

**预测 2002 年时，系统可访问数据空间内不存在任何 2002 年及以后才可获得的信息。**

证明链：
1. `create_snapshot` 按 `available_time ≤ 2001-12-31` 物理过滤（剔除 8,206 行）。
2. Rust `timeguard audit --snapshot asof_2001 --cutoff 2001-12-31` 全量扫描 5,434 行 `.timeindex`，violations=[]。
3. 黄金样例测试证明：2001 年报（2002-03-15 披露）在预测 2002 时被 `validate_row` 以 `future_data_leak` 拒绝。
4. 快照冻结（manifest sealed + SHA-256），后续 2002/2003 数据导入写入独立目录，无法改变 asof_2001。
5. 预测记录 SHA-256 存 SQLite，`verify_all()` 确认 0 篡改。

预测 2003/2004 同理（asof_2002/asof_2003 分别扫描 8,164 / 10,893 行零违规）。
