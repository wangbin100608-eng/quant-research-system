# v0.7.0 P0 现有模型审计

## 1. 模型实现（`python/quant/models.py`）

TRAINERS 注册 6 类：
- benchmark_mean（历史均值）
- timeseries_naive（上期均值）
- lightgbm / xgboost / catboost（树模型）
- torch_mlp（可选 PyTorch，CPU 版）

版本标识：benchmark_mean_v1 / timeseries_naive_v1 / lightgbm_v1 / xgboost_v1 / catboost_v1 / torch_mlp_v1。

## 2. 验证体系（`python/quant/validation.py`）

- 5 种切分：expanding_window、rolling_window、walk_forward、purged_embargo、按年份分组验证能力。
- 指标：MAE、RMSE、方向准确率、Rank IC、Spearman、区间覆盖率、Brier、分市场/行业；样本不足标 insufficient_evidence。
- 模型对比报告：`data/reports/model_comparison.json`（6 模型，训练≤2002，测试 2003-2004）。

## 3. 与 v0.7.0 P16 差距

| 要求 | 现状 |
|---|---|
| 每市场×每周期 6 组模型（A_1D/5D/20D、US_1D/5D/20D） | 未建立市场×周期独立模型，仅有统一模型 |
| Ridge / ElasticNet / LightGBM Ranker | 未实现 |
| 特征分组独立模型（价格/成交/横截面/事件/财务/宏观/综合） | 未实现 |
| Meta Model 仅用 OOF/OOS 预测 | 未实现 |
| 模型稳定性/漂移监控 | 未实现 |

## 4. 现存约束

- 样本量小（训练 21 / 测试 7 的真实年度数据），模型对比结论仅作事实记录，不宣称稳定预测能力。
- 模型注册表存 trained_to / feature_version / snapshot_id，但缺训练代码 commit / 选择依据 / 数据血缘报告。

结论：模型底座可用，v0.7.0 应按市场×周期扩展，不急于新增复杂网络；先补 Ridge/ElasticNet/Ranker 与分组对照。
