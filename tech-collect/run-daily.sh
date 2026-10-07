#!/bin/bash
# ============================================================
# 技术新闻每日采集 — 主编排脚本
# 用法: bash run-daily.sh [YYYY-MM-DD]
# 三个渠道：GitHub Trending + 外部技术博客 + X.com 技术观察
# ============================================================
set -uo pipefail

DATE="${1:-$(date +%Y-%m-%d)}"
BASE="$(cd "$(dirname "$0")" && pwd)"
OUT_GITHUB="$BASE/output/github"
OUT_BLOG="$BASE/output/blog"
OUT_X="$BASE/output/x"
SCRIPTS="$BASE/scripts"      # 可执行脚本
TMP="$BASE/tmp"              # 运行时中间数据
RAW="$TMP/raw/$DATE"
RAWX="$TMP/x-raw"
LLM_OUT="$TMP/llm-out/$DATE"
SUMMARY_DIR="$BASE/summary"
mkdir -p "$OUT_GITHUB" "$OUT_BLOG" "$OUT_X" "$RAW" "$RAWX" "$SUMMARY_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

echo "============================================="
log "技术新闻每日采集  $DATE"
echo "============================================="

# ---------- Part 1: GitHub Trending ----------
log "📦 Part 1: GitHub Trending"
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

curl -sL -A "$UA" "https://github.com/trending?since=daily" -o "$RAW/github-daily.html" 2>/dev/null
curl -sL -A "$UA" "https://github.com/trending?since=weekly" -o "$RAW/github-weekly.html" 2>/dev/null

if [ -s "$RAW/github-daily.html" ]; then
  python3 - "$RAW/github-daily.html" "$RAW/github-daily.json" << 'PYEOF'
import re, sys, json
html = open(sys.argv[1]).read()
parts = html.split('class="Box-row"')
repos = []
for part in parts[1:]:
    links = re.findall(r'href="/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)"', part)
    repo = None
    for m in links:
        if m not in ('trending/developers',) and not m.startswith('sponsors/') and '/' in m:
            repo = m
            break
    if not repo: continue
    # 实际仓库名：跳过 sponsor 链接，取真正的 repo link
    real = re.search(r'href="/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)"[^>]*class="Link"', part)
    if real: repo = real.group(1)
    desc_m = re.search(r'<p class="col-9 color-fg-muted my-1 tmp-pr-4">\s*(.*?)\s*</p>', part, re.DOTALL)
    desc = re.sub(r'<[^>]+>', '', desc_m.group(1)) if desc_m else ''
    desc = desc.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>').replace('&quot;','"')
    lang_m = re.search(r'programmingLanguage">(\w+(?:\s*\w+)?)', part)
    lang = lang_m.group(1) if lang_m else ''
    star_m = re.search(r'(\d[\d,]*)\s+stars? today', part)
    stars = int(star_m.group(1).replace(',','')) if star_m else 0
    repos.append({'repo': repo, 'desc': desc[:200], 'lang': lang, 'stars_today': stars})
repos.sort(key=lambda x: x['stars_today'], reverse=True)
json.dump(repos, open(sys.argv[2], 'w'), ensure_ascii=False, indent=2)
print(f"  GitHub: {len(repos)} 个仓库")
PYEOF
else
  log "  ⚠️ GitHub daily HTML 为空"
fi

# ---------- Part 2: 外部技术博客 ----------
log "📦 Part 2: 外部技术博客"
FEEDS="openai|https://openai.com/news/rss.xml
google-ai|https://blog.google/innovation-and-ai/technology/ai/rss/
simonwillison|https://simonwillison.net/atom/everything/
huyenchip|https://huyenchip.com/feed.xml
eugeneyan|https://eugeneyan.com/rss/
latent-space|https://www.latent.space/feed
microsoft-ai|https://news.microsoft.com/source/topics/ai/feed/
hn-frontpage|https://hnrss.org/frontpage
infoq-cn|https://www.infoq.cn/feed"

while IFS='|' read -r name url; do
  [ -z "$name" ] && continue
  # curl 带 2 次重试（网络偶发抖动）
  code=000
  for attempt in 1 2; do
    code=$(curl -sL -A "$UA" --connect-timeout 8 --max-time 15 -o "$RAW/blog-$name.xml" -w "%{http_code}" "$url" 2>/dev/null)
    [ "$code" = "200" ] && break
    sleep 2
  done

  # 判断内容是否为合法 RSS/Atom（HTTP 200 也可能是 Cloudflare 拦截页）
  is_rss=0
  if [ -f "$RAW/blog-$name.xml" ] && head -c 400 "$RAW/blog-$name.xml" | grep -qE '<\?xml|<rss|<feed|<channel'; then
    is_rss=1
    size=$(wc -c < "$RAW/blog-$name.xml")
  else
    size=0
  fi

  # 兜底：curl 非 200 或内容不是 RSS 时，改用浏览器抓取突破 Cloudflare
  if [ "$is_rss" = "0" ] && command -v mearl >/dev/null 2>&1; then
    bash "$SCRIPTS/fetch-via-browser.sh" "$url" "$RAW/blog-$name.xml" >/dev/null 2>&1
    if [ -f "$RAW/blog-$name.xml" ] && head -c 400 "$RAW/blog-$name.xml" | grep -qE '<\?xml|<rss|<feed|<channel'; then
      size=$(wc -c < "$RAW/blog-$name.xml")
      printf "  %-14s 🖥 mearl  %8s bytes  (curl %s → 浏览器兜底)\n" "$name" "$size" "$code"
    else
      printf "  %-14s ❌ 失败 (curl %s)\n" "$name" "$code"
    fi
  else
    printf "  %-14s HTTP %s  %8s bytes\n" "$name" "$code" "$size"
  fi
done <<< "$FEEDS"

# Anthropic News（HTML 源，非 RSS）：curl 带重试，失败走浏览器兜底
acode=000
for attempt in 1 2 3; do
  acode=$(curl -sL -A "$UA" --connect-timeout 10 --max-time 25 -o "$RAW/anthropic-news.html" -w "%{http_code}" "https://www.anthropic.com/news" 2>/dev/null)
  asize=0; [ -f "$RAW/anthropic-news.html" ] && asize=$(wc -c < "$RAW/anthropic-news.html")
  [ "$asize" -gt 5000 ] && break
  sleep 2
done
if [ "${asize:-0}" -gt 5000 ]; then
  printf "  %-14s HTTP %s  %8s bytes\n" "anthropic-news" "$acode" "$asize"
elif command -v mearl >/dev/null 2>&1; then
  bash "$SCRIPTS/fetch-via-browser.sh" "https://www.anthropic.com/news" "$RAW/anthropic-news.html" >/dev/null 2>&1
  asize=0; [ -f "$RAW/anthropic-news.html" ] && asize=$(wc -c < "$RAW/anthropic-news.html")
  printf "  %-14s 🖥 mearl  %8s bytes  (curl %s → 浏览器兜底)\n" "anthropic-news" "$asize" "$acode"
else
  printf "  %-14s ❌ 失败 (curl %s)\n" "anthropic-news" "$acode"
fi

# ---------- Part 3: X.com ----------
log "📦 Part 3: X.com（Mearl 复用浏览器登录态）"
if command -v mearl >/dev/null 2>&1; then
  conn=$(mearl browser_list 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('connectedCount',0))" 2>/dev/null || echo 0)
  if [ "${conn:-0}" -ge 1 ]; then
    bash "$SCRIPTS/x-collect.sh" 2>&1 | tail -25
  else
    log "  ⚠️ mearl 未连接浏览器（connectedCount=$conn），跳过 X.com。请先登录 Chrome 并启用 Mearl 扩展"
  fi
else
  log "  ⚠️ 未安装 mearl CLI，跳过 X.com"
fi

# ---------- Part 4: 本地 LLM 翻译/摘要预处理 ----------
log "📦 Part 4: 本地 LLM 预处理（翻译/摘要，替代云端）"

# 内存检测：估算 macOS 可用内存（GB），用于判断能否加载 9.2GB 的 qwen3.5-32k
mem_available_gb() {
  vm_stat 2>/dev/null | awk '
    /page size of/ { ps = $8 + 0 }
    /Pages free/ { free = $3 + 0 }
    /Pages inactive/ { inact = $3 + 0 }
    /Pages speculative/ { spec = $3 + 0 }
    /Pages purgeable/ { purge = $3 + 0 }
    END { if (ps == 0) ps = 16384; printf "%.1f", (free + inact + spec + purge) * ps / 1024/1024/1024 }'
}
mem_total_gb() {
  echo $(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1024 / 1024 / 1024 ))
}
ge() { python3 -c "import sys; sys.exit(0 if float('$1') >= float('$2') else 1)"; }

OLLAMA_STARTED_BY_US=""
OLLAMA_MEM_NEED_GB="${OLLAMA_MEM_NEED_GB:-10.0}"   # 模型 9.2GB + 余量

if [ "${ENABLE_LOCAL_LLM:-1}" = "1" ]; then
  # 1) ollama 是否在运行
  if ! curl -s --connect-timeout 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    AVAIL=$(mem_available_gb); TOTAL=$(mem_total_gb)
    log "  ollama 未运行（可用内存 ${AVAIL}GB / 共 ${TOTAL}GB，需求 ${OLLAMA_MEM_NEED_GB}GB）"
    if ge "$AVAIL" "$OLLAMA_MEM_NEED_GB"; then
      if command -v ollama >/dev/null 2>&1; then
        log "  → 内存充足，启动 ollama serve"
        nohup ollama serve > "$TMP/ollama-serve.log" 2>&1 &
        OLLAMA_STARTED_BY_US=$!
        for i in $(seq 1 30); do
          curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1 && break
          sleep 1
        done
        curl -s --connect-timeout 2 http://localhost:11434/api/tags >/dev/null 2>&1 \
          && log "  ✓ ollama 已就绪（PID $OLLAMA_STARTED_BY_US）" \
          || { log "  ⚠️ ollama 启动超时"; OLLAMA_STARTED_BY_US=""; }
      else
        log "  ⚠️ 未安装 ollama CLI，跳过"
      fi
    else
      log "  ⚠️ 内存不足（${AVAIL}GB < ${OLLAMA_MEM_NEED_GB}GB）→ 跳过本地预处理，云端兜底"
    fi
  fi

  # 2) 模型是否存在，不存在则一次性创建
  if curl -s --connect-timeout 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    if ! ollama list 2>/dev/null | grep -q 'qwen3.5-32k'; then
      log "  模型 qwen3.5-32k 不存在，从 qwen3.5:9b 创建（num_ctx=32768）"
      printf 'FROM qwen3.5:9b\nPARAMETER num_ctx 32768\nPARAMETER temperature 0.3\n' > "$TMP/Modelfile.qwen32k"
      ollama create qwen3.5-32k -f "$TMP/Modelfile.qwen32k" 2>&1 | tail -2
    fi
    # 3) 执行本地预处理
    python3 "$SCRIPTS/local-prep.py" all --date "$DATE" 2>&1 | tail -30
  else
    log "  ⚠️ ollama 不可用，跳过本地预处理（云端兜底）"
  fi
else
  log "  ⏭️ ENABLE_LOCAL_LLM=0，跳过本地预处理"
fi

# 4) 若 ollama 由本次任务启动，任务结束即卸载模型并关闭，释放内存
if [ -n "$OLLAMA_STARTED_BY_US" ]; then
  log "  ollama 由本次任务启动 → 卸载模型并关闭服务，释放内存"
  ollama stop qwen3.5-32k >/dev/null 2>&1 || true
  sleep 1
  kill "$OLLAMA_STARTED_BY_US" 2>/dev/null || true
  sleep 2
  kill -9 "$OLLAMA_STARTED_BY_US" 2>/dev/null || true
  log "  ✓ ollama 已关闭（PID $OLLAMA_STARTED_BY_US）"
fi

# ---------- 汇总与下一步 ----------
echo "============================================="
log "采集 + 预处理完成，汇总："
log "  原始数据: $RAW/"
log "  X 数据:   $RAWX/*.json ($(ls "$RAWX"/*.json 2>/dev/null | wc -l | tr -d ' ') 个账号)"
log "  本地富化: $LLM_OUT/{github,blog,x}.json"
log "  报告骨架: output/{github,blog,x}/$DATE-local9b.md"
echo ""
log "【下一步】云端模型：读 $LLM_OUT/*.json → 产出日报与总结"
log "  - $OUT_GITHUB/$DATE.md"
log "  - $OUT_BLOG/$DATE.md"
log "  - $OUT_X/$DATE.md"
log "  - $SUMMARY_DIR/$DATE.md          （每日总结）"
log "  - $SUMMARY_DIR/$DATE-weekly.md   （每周总结，周日生成）"
log "【最后】推送：bash $SCRIPTS/push-daily.sh $DATE"
echo "============================================="