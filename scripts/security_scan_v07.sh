#!/usr/bin/env bash
# v0.7 P20：只读密钥扫描（Git 历史 / 工作区 / Docker 配置 / CI 配置）
set -uo pipefail
cd "$(dirname "$0")/.."
echo "=== 1) 当前索引敏感项 ==="
git ls-files | grep -Ei '\.seal_key|\.env$|meta\.db$|\.pem$|\.key$' || echo "CLEAN"
echo "=== 2) Git 历史中出现过密钥的提交 ==="
git log --all --oneline -- data/.seal_key | head -5 || true
echo "=== 3) 工作区文件内容扫描（API key/token 模式） ==="
grep -rIEo '(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|api[_-]?key["'"'"']?\s*[:=]\s*["'"'"'][A-Za-z0-9]{16,})' \
  --include='*.py' --include='*.yaml' --include='*.md' --include='Dockerfile' . \
  2>/dev/null | grep -v '^./docs/V0.4.1_DELIVERY_REPORT.md' | head -10 || echo "NO_MATCH"
echo "=== 4) Docker / CI 配置 ==="
ls -la docker/ deploy/ .github 2>/dev/null | head -20 || true
echo "=== 5) 日志脱敏检查 ==="
grep -rlE '(ghp_|github_pat_|seal_key)' data/logs/ 2>/dev/null | head -5 || echo "NO_LOG_LEAK"
