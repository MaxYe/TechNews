#!/bin/bash
# ============================================================
# 每日推送采集内容到 GitHub
#
# 仓库: git@github.com:MaxYe/TechNews.git
#
# 设计说明：本仓库 .git 状态在会话间可能被回滚，因此脚本在
#           **单次执行内** 完成 init → remote → fetch → checkout →
#           add → commit → push 全流程，不依赖跨调用的本地状态。
#
# 用法:
#   bash scripts/push-daily.sh [YYYY-MM-DD]   # 日期用于 commit message
#   ENABLE_GIT_PUSH=0 bash scripts/push-daily.sh   # 跳过
# ============================================================
set -uo pipefail

BASE="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="${GIT_REMOTE:-git@github.com:MaxYe/TechNews.git}"
BRANCH="${GIT_BRANCH:-main}"
DATE="${1:-$(date +%Y-%m-%d)}"
GIT_ID_NAME="${GIT_ID_NAME:-MaxYe}"
GIT_ID_EMAIL="${GIT_ID_EMAIL:-MaxYe@users.noreply.github.com}"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

cd "$BASE" || exit 1

if [ "${ENABLE_GIT_PUSH:-1}" != "1" ]; then
  log "⏭️ ENABLE_GIT_PUSH=0，跳过推送"
  exit 0
fi

# ── 1. 确保仓库存在 ──
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "初始化 git 仓库"
  git init -q || { log "❌ git init 失败"; exit 1; }
fi

# ── 2. remote ──
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE" 2>/dev/null || git remote set-url origin "$REMOTE"

# ── 3. 拉取远端历史并基于其建分支（避免非快进被拒）──
if git fetch -q origin "$BRANCH" 2>/dev/null \
   && git rev-parse --verify "origin/$BRANCH" >/dev/null 2>&1; then
  git checkout -q -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || true
  log "已基于远端 $BRANCH 建分支（远端 $(git rev-parse --short origin/$BRANCH 2>/dev/null)）"
else
  log "⚠️ 无法拉取远端（首次推送或网络问题），直接提交"
  git checkout -q -B "$BRANCH" 2>/dev/null || true
fi

# ── 4. 暂存 ──
git add -A
if git diff --cached --quiet 2>/dev/null; then
  log "⏭️ 无变更，跳过推送"
  exit 0
fi
CHANGED=$(git diff --cached --name-only | wc -l | tr -d ' ')
log "暂存 $CHANGED 个文件"

# ── 5. 提交 ──
SCOPE=$(git diff --cached --name-only | sed 's|^tech-collect/||' | cut -d/ -f1 | sort -u | tr '\n' ' ')
git -c user.name="$GIT_ID_NAME" -c user.email="$GIT_ID_EMAIL" \
    commit -q -m "采集日报 $DATE

变更范围: $SCOPE
文件数: $CHANGED" || { log "❌ commit 失败"; exit 1; }
log "提交: $(git log --oneline -1)"

# ── 6. 推送 ──
if git push -q origin "$BRANCH" 2>/dev/null; then
  log "✅ 已推送到 $REMOTE ($BRANCH)，$CHANGED 个文件"
  exit 0
else
  log "❌ 推送失败（本地已提交，可手动 push）"
  exit 1
fi
