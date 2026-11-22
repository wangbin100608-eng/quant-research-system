# v0.2.0 阶段6 性能与内存优化报告

## 修改内容

1. **修复内存统计显示 0.0 MB**（`python/quant/utils.py`）
   - 根因：v0.1.0 的 `measure()` 每次 `tracemalloc.start()/stop()`；年度循环中内层 `stop()` 终止全局追踪，导致外层 peak=0。
   - 修复：引用计数的嵌套管理（只有最外层 start/stop）+ 同时记录 `ru_maxrss`（进程 RSS 峰值）。
   - SQLite `metrics` 表新增 `rss_mb`、`rss_delta_mb` 列。

2. **去重加载**：Polars `scan_parquet().unique()` / DuckDB `SELECT DISTINCT` 纳入基准，替代 Python `set(zip(...))`。

3. **Parquet 分区写入**：按 `market/year` 分区写 `data/clean/raw_real_partitioned/`（zstd）。

4. **列式分析工具**：Polars 惰性扫描 + DuckDB 列式聚合，均纳入统一基准。

5. **可重复基准脚本**：`python/scripts/perf_benchmark.py`，每任务重复 5 轮取中位数。

## 运行命令

```bash
PYTHONPATH=python python3 python/scripts/perf_benchmark.py
# 输出：data/reports/performance_report.json
```

## 关键性能数据（真实数据 12,716 行，5 轮中位数）

| 对比项 | baseline | optimized | 加速比 | 改善 |
|---|---|---|---|---|
| 列投影加载 | 0.0115s（全 11 列） | 0.0028s（4 列） | **4.11x** | ✅ |
| 聚合查询 | Python 0.0606s | Polars 0.0041s | **14.78x** | ✅ |
| 聚合查询 | Python 0.0606s | DuckDB 0.0351s | **1.73x** | ✅ |
| 去重加载 | Python 0.0999s | Polars 0.0057s | **17.53x** | ✅ |
| 去重加载 | Python 0.0999s | DuckDB 0.0362s | **2.76x** | ✅ |

**结果一致性**：Python / DuckDB / Polars 三种聚合结果完全相等（11 个 market×year 分组）；三种去重行数完全相等（12,700 行去重，12,716 总行）——全部 `true`。

**内存**：
- tracemalloc 对 Arrow/Polars/DuckDB 的 C++ 原生分配不计量（显示 0.0），这是工具局限而非真实零占用。
- 进程 RSS 峰值（`ru_maxrss`）如实记录（150~249MB，含 Python 解释器与所有已加载引擎），作为真实内存指标写入报告 `rss_mb`。
- Python 层去重 tracemalloc 峰值 3.82MB，列投影 <0.01MB，反映 Python 侧内存随列数下降。

## 结论

**体现了明确的性能改善**：列投影、去重、聚合查询均显著加速（1.7x~17.5x），且结果完全一致。分区写入产生 77 个分区文件（market×year），便于后续按分区裁剪。

需要注意：小数据量（12k 行）下 DuckDB 聚合加速有限（1.73x），大数据集优势会更明显；Polars 惰性扫描在本场景表现最佳。所有优化保持数据口径、目录结构与模型基准流程不变。

## 输出路径

- 性能报告：`data/reports/performance_report.json`
- 分区数据：`data/clean/raw_real_partitioned/`
