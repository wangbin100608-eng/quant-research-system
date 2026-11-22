# v0.2.0 阶段1 代码审计报告

审计日期：2026-11-22
范围：现有 v0.1.0 全部源码（3,184 行 Python/Rust）、SQLite 真实数据库、4 份 manifest.json、3 份预测记录、16 项测试。
方法：逐行实读源码 + 真实文件取证 + 隔离副本绕过实证。子代理并行深挖 Rust / KB / 预测 / 2004 根因。

---

## 1. 总体结论

v0.1.0 的**时间隔离设计思路正确**，数据物理过滤与 Python 侧门禁基本有效；但“不可变”“全量审计”“已冻结”三处宣传**强于实际实现**：

| 宣称 | 实际 | 严重度 |
|---|---|---|
| Rust TimeGuard 全量审计 | 只扫 .timeindex 时间字符串，**不比对 manifest 中的 SHA-256**；篡改 parquet 后仍 PASS | P0 |
| 预测记录不可修改 | 无密钥 SHA-256，且校验基准与数据同处可写 SQLite；**双端同步篡改可完整绕过** | P0 |
| 快照冻结后不可变 | sealed 仅 JSON 布尔；无 OS 只读、无签名；未 sealed 快照可被 rmtree 重建 | P1 |
| 2004 快照已纳入循环 | asof_2004 **永久未冻结**，且被审计/看板系统性跳过 | P1 |
| manifest 覆盖全部数据 | 仅 5 个文件（2004 仅 2 个）；.timeindex / model_registry / meta.json / audit **未纳入** | P1 |
| 16 项防泄漏测试充分 | 均为“正向通过”测试；无对抗性篡改、双端篡改、删行、竞态用例 | P1 |

---

## 2. Rust TimeGuard 审计（rust_core/src/main.rs）

### 实际行为
- `audit()` (L129-204) 递归收集 `*.timeindex` (L111-126)，逐行字符串比较 `value > cutoff` (L147) 与 unknown 检测 (L144)。
- **哈希**：`sha256_file()` (L38-...) 流式计算并打印，但 `audit()` **只打印 hashes 数组，从不与 manifest.file_hashes 比对** (L175-186)。即使传 `--manifest`，也只检查 `sealed` 布尔 (L160-165)。
- **实证**：向 `asof_2001/clean_data.parquet` 追加 7 字节垃圾后，`timeguard audit --manifest ... --require-sealed` 仍返回 `{"ok":true,...,"hashes":[["clean_data.parquet","2d9057..."]]}`（原 manifest 为 `654323...`）——篡改未被发现。同场景 Python `load_snapshot()` 正确抛出 `LeakError: snapshot tampered`。
- **Python 回退** (timeguard.py:143-161) 同样不校验 file_hashes，比 Python `load_snapshot` 宽松。

### 缺陷
- **P0** R1：Rust 不比对 manifest hash，审计可绕过。
- P1 R2：manifest 状态字符串匹配 `"sealed": false`，格式变化即误判。
- P1 R3：`.timeindex` 内容可被单独篡改（不在 hash 覆盖内），Rust 的输入本身不可信。
- P2 R4：只 hash 顶层 `*.parquet`，models/*.pkl 不在扫描范围。

---

## 3. 知识库快照与 manifest 审计（kb.py / storage.py）

### 实际行为
- `create_snapshot()` (kb.py:60-132) 以 `available_time <= cutoff` 物理过滤 (L75-83)，strict 下 unknown 抛错 (L78-80)。这是有效的第一道防线。
- `seal_snapshot()` (L147-168) 写 `sealed=True` + 时间戳，更新 SQLite，写 audit/seal.json。
- `load_snapshot()` (L171-193) 会校验 manifest 列出的每个文件 hash，但**主流程 orchestrator 步骤2 从未调用它**（orchestrator.py:104-113 只调 `audit_snapshot`），因此 hash 校验在年度循环中**实际未执行**。

### 真实 manifest 核对
| 快照 | sealed | entries | file_hashes | time_range | 缺项 |
|---|---|---|---|---|---|
| asof_2001 | true | 5434 | 5 | 2000-01-03 ~ 2001-12-31 | .timeindex/model_registry/meta.json/audit |
| asof_2002 | true | 8164 | 5 | ~ 2002-12-31 | 同上 |
| asof_2003 | true | 10893 | 5 | ~ 2003-12-31 | 同上 |
| asof_2004 | **false** | 13632 | **2** | ~ 2004-12-31 | 无 features/models/seal.json；hash 仅 clean_data+knowledge |

### 缺陷
- **P0** K1：主流程不调用 `load_snapshot`，manifest hash 校验形同虚设。
- P1 K2：create_snapshot 内部不过滤 knowledge_rows（依赖调用方），可绕过写入未来知识。
- P1 K3：model_registry 中 `trained_to` 为空即放行 (kb.py:95-96)。
- P1 K4：`parse_date` 对非法日期串未捕获异常，崩溃而非拒绝。
- P1 K5：hash 覆盖依赖 orchestrator 临时传参，缺 timeindex/model_registry/meta.json。
- P2 K6：未 sealed 快照被 `shutil.rmtree` 无痕重建；manifest/seal 写入非原子。

---

## 4. 预测记录不可变性审计（evaluation.py）

### 实证结果
在 /tmp 隔离副本上：同时修改 JSON 的 `result`、文件 `hash` 字段、SQLite 的 `result_hash/result_json/spec_json` 后，`verify_all()` 返回 `ok=True`。恢复时误操作曾污染宿主机数据，已从 Docker 卷完整恢复并复验 `ok=True`。

### 缺陷
- **P0** P1：无密钥 SHA-256，无 HMAC / 签名 / 外部锚定 → 可重签。
- **P0** P2：校验基准 `result_hash` 与数据同处一个可写 SQLite → 双端篡改绕过。
- P1 P3：`result_json/spec_json` 双份真相源写入后从不校验。
- P1 P4：`write_text` 非原子、无 fsync/锁；文件/DB 无事务；并发可覆盖。
- P1 P5：`verify_all` 不查 sealed 翻转、删行、孤立文件、链连续性、pred_id 一致性。
- P2 P6：测试 `test_predictions_are_immutable` 只改单侧，给出虚假安全感。

诚实定性：**v0.1.0 的“不可修改” = 防误改，不是密码学不可篡改。**

---

## 5. 2004 快照未冻结根因（orchestrator.py）

### 根因
循环边界设计缺陷，非随机 bug：
- 步骤7 `freeze_prediction` 冻结的是 `asof_{year-1}`（当前预测年所用快照）。
- 步骤16 `create_next_snapshot` 只创建 `asof_{year}`，初始 `sealed=false`，其冻结要等下一年步骤7。
- 配置 `last_prediction_year=2004`，不存在 2005 循环 → asof_2004 永久未冻结。

逐年映射（system.log 证据）：2002→冻 asof_2001/建 asof_2002；2003→冻 asof_2002/建 asof_2003；2004→冻 asof_2003/建 asof_2004（之后无 seal）。

### 放大问题的跳过路径
- `dashboard/app.py:127` 审计多选框默认 `[2001,2002,2003]`，排除 2004。
- `test_report.py:38` 时间审计循环也只 `(2001,2002,2003)`，16/16 验收未覆盖此缺陷。
- asof_2002/2003 在“生成后到下一年冻结前”存在可被 rmtree 重建的暴露窗口。

---

## 6. 测试审计（test_leakage.py + test_temporal_semantics.py，16 项）

| 类别 | 用例 | 真实覆盖 |
|---|---|---|
| 训练集未来数据 | test_future_data_not_in_training_set | 读真实 clean_data 断言 ≤ cutoff ✓ |
| 单行门禁 | test_future_row_rejected / unknown_rejected | 调 validate_row ✓ |
| 知识文档 | test_future_knowledge_not_in_snapshot | 读 knowledge.parquet ✓ |
| 知识图谱 | test_future_graph_node_rejected | **仅断言 vector_enabled=false + 规则日期**，未真正查询图 ✗ |
| 财报 | test_future_fundamental_not_in_features | 仅查 asof_date，未验证 EPS 来源披露日 ✗（另有独立语义测试补强） |
| 特征归一化 | test_feature_cache_scoped_to_snapshot | 仅查文件存在 + manifest 版本字段 ✗ |
| 模型权重 | test_future_model_weight_rejected / mismatch | validate_model 门禁 ✓ |
| 缓存 | test_cache_paths_are_snapshot_scoped | 仅查路径存在 ✗ |
| 预测不可变 | test_predictions_are_immutable | 单侧篡改 ✓（双端绕过未测） |
| Rust 审计 | test_rust_audit_passes_all_snapshots | 仅 PASS 路径，未注入未来数据/篡改 ✗ |

结论：16 项测试**覆盖 8 类要求的外壳**，但多为“读文件断言/正向路径”，缺少对抗性注入。v0.2.0 需补真实注入测试。

---

## 7. 数据真实状态

- fixture 合成数据 13,640 行；`source_runs` 宿主机 0 条、容器卷 2 条（两份 meta.db 不一致，审计期间已统一为容器卷）。
- 四个真实公网源实测可达：FRED(200/269KB)、SEC(200/164KB)、东方财富(200/25KB)、Yahoo Finance(200/139KB) → 第三阶段可接真数据。
- xgboost 3.4.1 / catboost 1.2.10 已安装，供第五阶段使用。

---

## 7b. Rust 审计补充实证（子代理深挖）

- SHA-256 自实现经 16 组向量（空/abc/边界长度/1MB 随机/真实 parquet）与 hashlib **全部一致**，算法本身正确；隐患仅在 `total*8` 理论溢出与无单测。
- **日期字符串字典序比较**（main.rs:147）：`20011231`（=2001-12-31）被误判 future_data_leak（'1'>'-'）；非标准串可蒙混。
- **sealed 子串匹配绕过**（main.rs:160）：`{"sealed": 0}`、`{"sealed" : false}`、manifest 文件缺失 → `--require-sealed` 均返回 rc=0。
- 文件读取 IO 错误静默跳过（main.rs:119/137/139）→ “少扫即通过”，无 fail-closed。
- Python 回退与 Rust 语义不一致：date 对象 vs 字符串、unknown 严格性、大小写集合、异常类型、返回结构均不同，声称“行为一致”不成立。

## 8. v0.2.0 修复优先级

**P0（必须）**
1. Rust audit 真正比对 manifest.file_hashes（含 models/*.pkl、timeindex、model_registry）。
2. 预测记录升级 HMAC-SHA256 + 链哈希 + append-only 触发器 + 原子落盘。
3. 年度循环步骤2 调用 load_snapshot，主流程强制 hash 校验。

**P1**
4. 末位快照补 seal；seal 生命周期原子化；hash 全目录覆盖。
5. create_snapshot 内部过滤知识 + 严格 parse_date + trained_to 缺失拒绝。
6. 新增对抗性防泄漏测试（注入未来数据、篡改文件、双端篡改、删行、2004 快照）。
7. 统一宿主机/容器数据库状态。

**P2**
8. checkpoint 输入指纹、产物哈希校验、知识条目去重。
9. 测试报告纳入 seals 与 2004 审计。
