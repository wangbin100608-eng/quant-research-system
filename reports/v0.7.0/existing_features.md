# v0.7.0 P0 现有特征审计

## 1. 现有特征实现

- `python/quant/features.py:71` `build_features(snapshot_path, spec)`：年度面板特征，字段为 trailing_mean_return_1y/2y、trailing_vol_1y、momentum_6m、avg_turnover_log、eps_latest（FEATURE_NUMERIC 6 项）。
- `python/quant/horizons.py:27` HORIZONS：1d/5d/20d/1m/3m/6m/1y 七种期限；每样本记录 feature_time / target_start / target_end，供 Purged/Embargo 使用。
- `FEATURE_VERSION = feature_v1`，与模型注册绑定。

## 2. 特征类别覆盖（对照 v0.7.0 P5）

| 类别 | 现状 |
|---|---|
| 价格特征 | 有（收益率/波动/动量），无分钟级、无多周期反转/跳跃/分位数 |
| 成交特征 | 仅有 avg_turnover_log，无成交额变化/异常度/量价相关 |
| 微观结构 | 无（订单簿数据本身也未获取，按规则禁止虚构） |
| 横截面 | 无独立特征，年度预测用横截面均值 |
| 公司事件 | 无独立事件特征（SEC filing 仅作数据） |
| 财务特征 | eps_latest 代理，无完整财务比率 |
| 宏观特征 | eps_latest 在真实数据管道中由 DGS10 利率代理，命名误导 |
| 特征注册表 | 无（feature_id/version/code_hash/availability_rule 等未登记） |

## 3. 主要缺口

1. 无统一 Feature Factory 与 feature registry；训练与预测调用同一份特征代码尚未形式化。
2. 无分钟/周/月频率特征；标签仅有日级衍生。
3. 横截面排名、行业相对收益未实现。
4. 财务比率与宏观特征不完整，且 DGS10→eps_latest 的代理命名需纠正。
5. 特征缺失策略与归一化参数未做 PIT 版本绑定。

## 4. 迁移建议

- 在 `src/features/` 建立 registry（feature_id、category、lookback、availability_rule、code_hash）。
- 复用 horizons 的 target 边界机制扩展 P5.1-P5.7 特征。
- 严禁在订单簿数据缺失时生成微观结构特征。
