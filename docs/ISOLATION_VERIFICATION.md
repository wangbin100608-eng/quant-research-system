# v0.2.0 阶段2 知识库时间隔离验证报告

## 验证方法

不满足于“检查配置文件”，采用三层实证：

1. **真实快照逐行扫描**：对 asof_2001/2002/2003 的全部 `.timeindex` 与 parquet 的 `available_time` 列逐年断言 ≤ cutoff。
2. **注入式对抗测试**（`test_isolation_v02.py`，15 项）：把带未来时间的行、unknown、非法日期格式主动写进快照副本，验证 Rust TimeGuard **确实阻断**。
3. **篡改测试**：篡改 parquet、删除 manifest、伪造 `sealed:0`，验证 P0 缺陷已修复。

## 逐年隔离结论

| 预测年 | 截止日期 | 快照 | 行数 | 最大 available_time | 超界 | Rust 审计 |
|---|---|---|---|---|---|---|
| 2002 | 2001-12-31 | asof_2001 | 5434 | 2001-12-31 | 0 | PASS |
| 2003 | 2002-12-31 | asof_2002 | 8162 | 2002-12-31 | 0 | PASS |
| 2004 | 2003-12-31 | asof_2003 | 10890 | 2003-12-31 | 0 | PASS |

三年均确认：可访问数据空间内不存在 cutoff 之后的信息。

## 全类型数据覆盖检查

- 原始行情 / 清洗数据（`clean_data.parquet`）：available_time ≤ cutoff ✓
- 知识文档（`knowledge.parquet`）：available_time ≤ cutoff ✓
- 特征与归一化（`features.parquet`）：asof_date ≤ cutoff，缓存路径绑定快照 ✓
- 模型权重 / 元数据（`model_registry.json`）：trained_to ≤ cutoff ✓
- 向量索引：v0.1.0 未启用（vector_enabled=false），无跨快照检索 ✓
- 知识图谱：知识条目表同样受 available_time 过滤 ✓
- `.timeindex`：由 clean/knowledge 的 available_time 生成，Rust 全量扫描 ✓

## 对抗测试结果（15/15 PASS）

- 注入未来行（2002/2003/2004 三年）→ Rust 抛 `future_data_leak` ✓
- 注入 `unknown` → `unknown_availability` ✓
- 注入非法日期 `20011231` → `invalid_date_format`（v0.1.0 此处会误判或蒙混）✓
- 篡改 parquet → `manifest_hash_mismatch`（P0 R1 修复）✓
- 删除 manifest + require_sealed → 阻断（P0 sealed 缺失绕过修复）✓
- `sealed:0` + require_sealed → 阻断（P0 sealed 子串绕过修复）✓
- KB `verify_snapshot` 检测未来行 ✓

## v0.2.0 代码修复清单（已落地）

### Rust（rust_core/src/main.rs）
- 日期改为严格 `(y,m,d)` 解析 + 非法格式拒绝（R1）
- manifest 用最小 JSON 解析器取字段，`sealed` 必须是布尔 `true`，缺字段/文件 fail-closed（R2）
- 逐项重算 `manifest.file_hashes` 并比对，篡改即 violation（R3）
- `.timeindex` IO 错误 fail-closed；空扫描/低于 entry_count 拒绝（R4）
- JSON 输出转义；CLI 参数边界检查；NIST SHA-256 自测向量 `timeguard selftest`（R5）

### Python
- `kb.create_snapshot`：内部强制过滤知识条目；非法日期 fail-closed；model trained_to 缺失严格拒绝
- `kb.hash_snapshot_dir`：全目录哈希（含 .timeindex / model_registry / audit / models）
- `kb.seal_snapshot`：始终以真实全目录哈希为准，忽略过时入参
- `kb.verify_snapshot` / `restore_snapshot_from_manifest`：恢复与重新验证 API
- `kb.load_snapshot`：全目录双向校验（检测新增/删除/修改）
- `timeguard.audit_snapshot`：Python 回退与 Rust 语义对齐（严格日期 + 全目录 hash）
- `orchestrator`：步骤2 强制 `verify_snapshot`；末位快照补 seal（修复 2004 未冻结）

## 复现命令

```bash
./rust_core/target/release/timeguard selftest
PYTHONPATH=python python3 -m pytest python/tests/test_isolation_v02.py -v
```
