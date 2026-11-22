# TESTING（v0.5.0）

## 测试分层

- `test_leakage.py` / `test_isolation_v02.py`：防泄漏基础与注入测试。
- `test_time_v04.py`：UTC 时间内核、Rust/Python cutoff 一致。
- `test_acceptance_v04.py` / `test_acceptance_v05.py`：验收系统自身的负向测试（篡改 FAIL、模块缺失 FAIL、非零退出码）。
- `test_freeze_v05.py`：预测冻结溯源、append-only、修正原因。
- `test_provenance_v05.py`：Embedding 时间隔离、模型权重溯源、Agent 越权日志。
- `test_stream_v05.py`：流式读取与全量读取结果一致性、不同 batch_size。
- `test_data_quality_v05.py`：历史股票池与数据质量评分。
- `test_validation_v03.py` / `test_macro_vintage_v03.py` / `test_knowledge_v03.py` / `test_security_v03.py`：验证切分、宏观版本、六库、安全。

## 执行命令

```bash
PYTHONPATH=python python3 -m pytest python/tests -q
PYTHONPATH=python python3 -m coverage run --source=python/quant -m pytest python/tests -q
PYTHONPATH=python python3 -m coverage report
PYTHONPATH=python python3 python/scripts/final_acceptance_v05.py   # exit 0=PASS, 1=FAIL
rust_core/target/release/timeguard selftest
```

## 当前基线（v0.5.0 最终）

94 passed；覆盖率 35%；验收 4/4 PASS；Rust selftest PASS。
覆盖率目标（核心模块 90-95%）尚未达成，见 `data/reports/coverage_report_v05.txt` 与交付报告 NOT_TESTED 说明。
