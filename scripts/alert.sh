#!/usr/bin/env bash
# 任务失败告警钩子：可对接 webhook / 邮件 / 钉钉
# 用法: bash scripts/alert.sh "任务名" "错误信息"
set -uo pipefail
TASK="${1:-unknown}"
MSG="${2:-}"
WEBHOOK="${ALERT_WEBHOOK:-}"
LOG="$(cd "$(dirname "$0")/.." && pwd)/data/logs/alerts.log"
mkdir -p "$(dirname "$LOG")"
echo "[$(date -Is)] ALERT task=$task msg=$MSG" | tee -a "$LOG"
if [ -n "$WEBHOOK" ]; then
  curl -s -X POST -H 'Content-Type: application/json' \
    -d "{\"msgtype\":\"text\",\"text\":{\"content\":\"[量化研究系统告警] $TASK: $MSG\"}}" \
    "$WEBHOOK" >/dev/null 2>&1 || true
fi
