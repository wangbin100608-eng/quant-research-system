# v0.2.0 阶段3 真实数据源接入报告

## 原则

- 真实数据写 `data/raw/raw_real.parquet`，fixture 合成数据写 `data/raw/raw_fixture.parquet`，**物理分离、绝不混写**。
- 所有真实数据行 `source` 前缀 `real:`、payload 标 `synthetic=false`、`data_origin=real`。
- 覆盖率报告按 `(market, year, domain)` 统计实际行数；缺失范围明确列出，不用合成数据补齐。

## 适配器清单（每个源独立）

| 适配器 | 数据源 | 获取内容 | 真实行数 |
|---|---|---|---|
| `eastmoney_cn.py` | 东方财富公开 kline API | A股上证指数/深证成指 2000-2004 日线 + 基本信息 | 2,403 |
| `yahoo_us.py` | Yahoo Finance chart API | 美股 SPX/IXIC/DJI/AAPL/MSFT 2000-2004 日线 + 基本信息 | 6,285 |
| `fred_v2.py` | FRED graph CSV | DGS10/DGS2/CPI/GDP/UNRATE/FEDFUNDS/DEXCHUS 2000-2004 | 3,955 |
| `sec_v2.py` | SEC EDGAR submissions（含 older 文件） | AAPL/MSFT/GOOGL 的 10-K/10-Q/8-K **真实披露日** | 73 |
| `news_v2.py` | GDELT DOC API | 历史新闻（published_time=seendate） | 0（见缺口） |
| `fixture.py` | 合成（仅验收用） | 全类型合成数据 | 13,640（独立文件） |

**合计真实数据：12,716 行**（抓取 10.8s，内存峰值 30.8MB）。

## 覆盖率（节选，完整见 coverage_report_real.json）

| 市场 | 年份 | 覆盖域 |
|---|---|---|
| CN_A | 2000-2004 | price（上证/深证各 1200 根日线）、info |
| US | 2000-2004 | price（5 标的）、info、macro（FRED 7 序列）、fundamental/announcement（SEC 披露） |
| GLOBAL | — | news（0，历史新闻源缺口） |

## 明确的数据缺口（未伪造）

1. **沪深300（SH000300）**：2005-04 才发布，2000-2004 无数据——这是真实历史事实，接口返回空，记为缺口。
2. **A股历史指数成分股**：东方财富该接口不稳定，未获取。
3. **退市股票名单**：需专门接口，未获取（fixture 中的退市股为合成，不混入真实数据）。
4. **A股财报真实披露日**：需专门接口，未获取（SEC 覆盖美股）。
5. **新闻 2000-2004**：GDELT 等公开 API 对该时段覆盖不完整，返回 0，明确标注。
6. **FRED vintage**：使用 fredgraph 估计发布滞后（标 `publication_lag=estimated`），非 ALFRED 原始 vintage。

## 五时间字段真实性

- 行情：`available_time = event_time = 交易日`（收盘后可得），source=real:*
- 财报：`event_time = reportDate`，`available_time = filedDate`（真实披露日），SEC 适配器计算 disclosure_lag_days
- 宏观：`event_time = 观测期`，`available_time = 发布日`（FRED 估计滞后，标注）
- 新闻：`available_time = seendate`

## 复现

```bash
PYTHONPATH=python python3 python/scripts/fetch_real.py
cat data/reports/coverage_report_real.json
```
