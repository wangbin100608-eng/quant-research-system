# v0.2.0 阶段5 模型升级与基准对比报告

## 升级内容

在 v0.1.0 的 benchmark mean 基础上，`models.py` 新增：

| 模型 | 版本标识 | 状态 |
|---|---|---|
| 历史均值（基准） | benchmark_mean_v1 | ✓ |
| 时序基准（naive AR1） | timeseries_naive_v1 | ✓ 新增 |
| LightGBM | lightgbm_v1 | ✓（v0.1 已有，统一接口）|
| XGBoost | xgboost_v1 | ✓ 新增（xgb 3.4.1）|
| CatBoost | catboost_v1 | ✓ 新增（catboost 1.2.10）|
| PyTorch MLP | torch_mlp_v1 | ✓ 新增（torch 2.15.0+cpu，逐环境检测）|

所有模型共用 `FEATURE_NUMERIC`（6 个特征）、`trained_to <= cutoff` 门禁、特征版本与快照绑定。

## 基准对比（真实数据，统一时间边界）

- 数据源：`data/raw/raw_real.parquet`（真实，非合成）
- 训练期：可得信息 ≤ 2002-12-31（21 个样本）
- 测试期：2003-2004（7 个样本）
- 严格点位时间隔离：是

| 模型 | MAE | RMSE | 方向准确率 | 训练耗时 |
|---|---|---|---|---|
| benchmark | 0.3632 | 0.7669 | 0.57 | 0.0s |
| timeseries | 0.4852 | 0.7212 | 0.57 | 0.001s |
| lightgbm | 0.3632 | 0.7669 | 0.57 | 2.265s |
| xgboost | 0.3843 | 0.8018 | **0.71** | 0.078s |
| catboost | 0.3720 | **0.7606** | 0.57 | 0.613s |
| torch_mlp | 0.4338 | 0.8533 | 0.43 | 8.461s |

结论（如实）：样本量很小（训练 21 / 测试 7），树模型未显著优于简单基准；XGBoost 方向准确率最高（0.71），CatBoost RMSE 最低。**不为增加模型数量牺牲可复现性**——所有模型固定随机种子 20000101、单线程、参数落盘。

## 复现

```bash
PYTHONPATH=python python3 python/scripts/model_benchmark.py
cat data/reports/model_comparison.json
```
