#!/bin/bash
# ============================================================
# 浏览器抓取兜底 — 用 mearl 突破 Cloudflare / 登录墙
# 用法: bash fetch-via-browser.sh <url> <输出文件>
# 原理: 在页面上下文内 fetch()，自动携带浏览器会话 Cookie
# ============================================================
set -uo pipefail

URL="$1"
OUT="$2"

if [ -z "$URL" ] || [ -z "$OUT" ]; then
  echo "用法: bash fetch-via-browser.sh <url> <输出文件>" >&2
  exit 1
fi

command -v mearl >/dev/null 2>&1 || { echo "未安装 mearl" >&2; exit 1; }

# 解析 origin（同源跳板）与 host（授权用）
read -r ORIGIN HOST <<< "$(python3 -c "
import urllib.parse
u = urllib.parse.urlparse('$URL')
print(u.scheme + '://' + u.netloc, u.netloc)
")"

# 授权域名（幂等）
mearl request_domain_permission --payload "{\"domain\":\"$HOST\"}" >/dev/null 2>&1

# 打开同源页面作为跳板
TAB=$(mearl tab_open --payload "{\"url\":\"$ORIGIN\",\"reuse\":\"prefer\"}" 2>/dev/null \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('tabId',''))" 2>/dev/null)
if [ -z "$TAB" ]; then
  echo "FAIL 无法打开跳板页 $ORIGIN" >&2
  exit 1
fi

sleep 3

# 生成页面内 fetch 表达式
python3 - "$URL" "$TAB" << 'PYEOF'
import json, sys
url, tab = sys.argv[1], sys.argv[2]
expr = (
    "fetch(" + json.dumps(url) + ")"
    ".then(function(r){return r.text().then(function(t){return {status:r.status,len:t.length,body:t};});})"
    ".then(function(o){return JSON.stringify(o);})"
)
open('/tmp/_bv_payload.json', 'w').write(json.dumps({'tabId': int(tab), 'expression': expr, 'awaitPromise': True}))
PYEOF

# 执行并落盘
mearl page_eval --payload-file /tmp/_bv_payload.json 2>/dev/null | python3 -c "
import sys, json
out = '''$OUT'''
try:
    d = json.load(sys.stdin)
    r = json.loads(d['result'])
    if r.get('status') == 200 and r.get('len', 0) > 0:
        open(out, 'w', encoding='utf-8').write(r['body'])
        print(f'OK {r[\"len\"]} bytes')
    else:
        print(f'FAIL status={r.get(\"status\")} len={r.get(\"len\")}')
        sys.exit(1)
except Exception as e:
    print(f'FAIL {e}')
    sys.exit(1)
"