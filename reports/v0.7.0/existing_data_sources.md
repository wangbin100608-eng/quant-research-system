# v0.7.0 P0 现有数据源审计

## 1. 适配器清单（18 个文件）

| 类别 | 适配器 | 数据源 | 状态 |
|---|---|---|---|
| A 股指数 | `eastmoney_cn.py`、`tencent_index.py`、`eastmoney_expand.py` | 东方财富、腾讯财经 | 上证/深证 2000-2004 各 1200 根真实日线；个股/沪深300等 BLOCKED |
| A 股基础 | `akshare_cn.py`、`akshare_extended.py` | AKShare | 退市 374、指数成分 850（当前名单）、行业 31、财报披露 500 |
| 美股 | `yahoo_us.py`、`sec.py`/`sec_v2.py`/`sec_v3.py` | Yahoo Finance、SEC EDGAR | 16 标的日线、20 公司 filing（真实披露日） |
| 宏观 | `fred.py`、`fred_v2.py`、`alfred_vintage.py` | FRED / ALFRED | ALFRED 22,598 条真实 vintage；FRED estimated 标禁止 |
| 新闻 | `news_v2.py` | GDELT | 历史覆盖缺口，未获取 |
| 合成 | `fixture.py` | 合成 | 仅验收，物理隔离 |

## 2. 数据规模与覆盖

- `raw_v03.parquet`：44,021 行真实数据，5 源（eastmoney_expand/yahoo_us/alfred_vintage/akshare_extended/sec_v3），synthetic=false。
- 覆盖率报告：`coverage_report_v03.json`（市场×年×域矩阵）、`coverage_report_v04_indices.json`、`a_share_price_gap.json`。
- 数据现状：`data_status_v060.json`（quality 77.8，真实/估算/合成区分）。

## 3. BLOCKED（外部条件）

1. A 股个股真实日线（东财 kline 限流）。
2. 沪深300/中证500/中证1000 2000-2004（发布较晚）。
3. 历史指数成分/行业版本（仅当前名单）。
4. A 股 2000-2004 财报披露完整历史版（AKShare 特定期数）。
5. 历史新闻（GDELT 限流/无覆盖）。

## 4. v0.7.0 P2 差距

- 未接入 Tushare Pro（需 token/权限，官方文档未核验）。
- 未接入 Alpaca Market Data（1 分钟 bars，需 API key；IEX/SIP 差异未处理）。
- 无统一 `DataProvider` 接口（get_data/get_metadata/get_coverage/health_check/get_rate_limit）。
- 无原始响应/文件哈希与断点续传的标准化实现（东财适配器已有 SHA-256 雏形）。
- 交易所官网（SSE/SZSE/CSRC）仅列为备用，未实现适配器。

结论：数据源以公开免费接口为主，覆盖率诚实但有限；v0.7.0 需先建立 Provider 抽象，再按权限实际情况接入 Tushare/Alpaca，不得假装已接入付费数据。
