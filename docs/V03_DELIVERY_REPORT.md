# Quant Research System v0.3.0 交付报告

基于 v0.2.0 增量升级（未重写），仓库：https://github.com/wangbin100608-eng/quant-research-system

## P0 完成情况

### P0-1 数据覆盖扩展
- **44,021 行真实数据**（`data/raw/raw_v03.parquet`，synthetic=false）
- 独立适配器：`alfred_vintage` / `eastmoney_expand` / `akshare_extended` / `sec_v3` / `yahoo_us`（扩展至 16 标的）
- 新增覆盖：沪市退市 161 + 深市退市 213、沪深300/上证50/中证500 成分 850、申万行业 31、A股财报披露 500、SEC 公司 3→20（716 条披露）
- 覆盖率报告 `coverage_report_v03.json` 按 市场×年份×类别 输出；缺口明确列出
- **如实记录缺口**：东财 kline 接口限流导致 v0.3 未新增 A股个股/指数日线；v0.2 的 A股日线为 fixture 合成，**未混入真实数据**（`a_share_price_gap.json`）

### P0-2 ALFRED 宏观历史版本
- `alfredgraph.csv?vintage_date=` 获取 **22,598 条真实版本化观测**，7 个序列 × 多个 vintage
- 每条保存：`original_value`（该版本原值）、`vintage_date`、`revision_time`、`time_basis=real_published`
- 实证同一观测在不同 vintage 值不同（2000-01 CPI：169.20→169.40→169.30）
- 严格模式门禁：`time_basis ∈ {estimated, unknown, imputed}` 一律拒绝（4 项测试）

### P0-3 多期限训练样本
- `horizons.py`：7 种期限（1d/5d/20d/1m/3m/6m/1y），独立目标变量
- 每样本记录 `feature_time` / `target_start` / `target_end`；step 采样降低重叠密度

### P0-4 模型验证系统
- 5 种 split：Expanding / Rolling / Walk-forward / Purged / Embargo
- Purged 实证：训练样本 target_end ≥ 测试起点必须剔除；Embargo 追加 gap
- 指标：MAE/RMSE/方向准确率/Rank IC/Spearman/区间覆盖率/Brier/分市场分组
- 所有模型与 benchmark 对比；样本 <30 或 |IC|<0.05 明确标记 `insufficient_evidence`
- 独立最终测试集不参与超参选择；7 期限 × 4 split × 3 模型报告在 `validation_report_v03.json`

## P1 完成情况

- **六库**：structured/document/vector/event/hypothesis/rule，统一 source/published/available/version/snapshot_id，按 snapshot 物理隔离，严格时间门禁
- **8 个 Agent**：数据分析/新闻事件/财报/宏观/因子/模型评估/研究批判/知识管理；LLM 仅提假设/解释/整理（可注入，默认确定性 stub），结构化评分由程序完成；Agent 不得直写 validated 规则
- **假设管理**：完整字段 + pending/validated/rejected/deprecated 状态机；失败假设保留 + 历史轨迹
- **安全完整性**：发现 `.seal_key` 曾被 GitHub 跟踪 → 已轮换密钥（旧密钥归档验签）、从 Git 移除并加入 .gitignore、独立备份签名记录到 `/root/工作区/quant_seal_backup/`；快照恢复/篡改检测测试通过

## P2 完成情况

- Rust+Python 架构保留；列投影 6.95x、DuckDB 聚合 5.76x（44k 行，5 轮中位数）
- 报告 `performance_report_v03.json` 含运行时间/峰值 RSS/磁盘占用(0.57MB)/结果一致性(Rust hash == Python hash)

## 最终验收

**14/14 PASS**，`data/reports/final_acceptance_v03.json`；59 项 pytest 全过。

## 复现命令

```bash
cd /root/工作区/股票
PYTHONPATH=python python3 python/scripts/fetch_v03.py
PYTHONPATH=python python3 python/scripts/validation_report.py
PYTHONPATH=python python3 python/scripts/perf_v03.py
PYTHONPATH=python python3 python/scripts/test_report_v03.py
PYTHONPATH=python python3 python/scripts/final_acceptance_v03.py
PYTHONPATH=python python3 -m pytest python/tests -q
```

## 已知缺口（未用合成数据补齐）

1. A股个股/指数日线（v0.3 抓取时东财限流）
2. 历史指数成分股（仅当前名单 + 纳入日期）
3. 历史行业分类版本（仅当前申万分类）
4. A股 2000-2004 财报披露的完整历史版（AKShare 仅提供特定期数）
5. FRED 非 vintage 序列的估计发布时间数据（严格模式禁用）
