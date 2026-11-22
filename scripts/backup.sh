#!/usr/bin/env bash
# v0.2.0 自动备份：数据库 + HMAC 密钥 + 完整快照 + 预测链 + 真实数据 + 报告
# 用法: bash scripts/backup.sh [backup_dir]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${1:-$ROOT/data/backups}"
TS="$(date +%Y%m%d_%H%M%S)"
DEST="$BACKUP_DIR/$TS"
mkdir -p "$DEST"

# 数据库
cp "$ROOT/data/meta.db" "$DEST/meta.db" 2>/dev/null || true
cp "$ROOT/data/analytics.duckdb" "$DEST/analytics.duckdb" 2>/dev/null || true
# HMAC 签名密钥（丢失将无法验证预测签名）
cp "$ROOT/data/.seal_key" "$DEST/.seal_key" 2>/dev/null || true
# 完整快照（不只是 manifest，恢复需全部文件）
[ -d "$ROOT/data/knowledge/snapshots" ] && cp -r "$ROOT/data/knowledge/snapshots" "$DEST/snapshots"
# 预测记录 / 检查点 / 报告 / 元数据
for d in predictions checkpoints reports; do
  [ -d "$ROOT/data/$d" ] && cp -r "$ROOT/data/$d" "$DEST/$d"
done
# 真实原始数据（较大但必要）
cp "$ROOT/data/raw/raw_real.parquet" "$DEST/raw_real.parquet" 2>/dev/null || true

# 保留最近 10 份
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d | sort -r | tail -n +11 | xargs -r rm -rf
echo "BACKUP_OK $DEST"
