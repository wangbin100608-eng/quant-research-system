# v0.7.0 P0 安全审计报告

## 1. 密钥与 Git 历史

- 当前工作区含 `data/.seal_key`、`data/.seal_key.retired`（HMAC 签名密钥，chmod 600）。
- Git 索引：当前 HEAD 中无 `.seal_key`、`meta.db`、`data/raw/`、`data/backups/`（远端文件树核验 NONE）。
- Git 历史：v0.2.0 提交 `c698538` 曾包含 `.seal_key`；v0.3.0 已轮换密钥并从索引移除，但**旧密钥仍存在于 Git 历史中**（公开仓库可获取）。这是已知安全事实，不是当前索引泄露。
- `data/.gitignore` 规则排除密钥/数据库/原始/快照/备份/缓存。

## 2. 当前保护机制

- HMAC-SHA256 + 链哈希 + append-only 触发器（`evaluation_v02.py`、`prediction_freeze_v05.py`）。
- 密钥版本字段（v0.6 新增），支持轮换；验签尝试所有历史密钥。
- 独立签名备份：`/root/工作区/quant_seal_backup/`（含 old_key 与预测快照，本地不上传）。

## 3. 风险

1. Git 历史中的旧密钥已公开 → 任何依赖该旧密钥签名的历史记录不可再视为密码学不可篡改；v0.3 已用新密钥重签，但旧记录签名理论上可被拥有历史密钥者重签。
2. 公开仓库/README 含公网 URL 与路径信息，不含密钥本体。
3. Docker 镜像层/CI 日志未扫描（NOT_TESTED，v0.6 交付报告已标注）。

## 4. 建议

- 若 v0.7.0 需要正式安全声明：再次轮换密钥、重写 Git 历史（需人工确认，纪律要求不改写历史）。
- 将签名记录备份保留独立存储；密钥与环境隔离（QUANT_SEAL_KEY 环境变量）。
- 纳入 CI 密钥扫描与日志脱敏（P20）。
