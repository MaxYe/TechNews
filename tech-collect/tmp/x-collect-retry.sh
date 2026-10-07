#!/bin/bash
# 补采脚本 — 带轮询等待，导航后轮询直到推文加载
WS="/Users/yefan/Works/TechNews/tech-collect"
RAW="$WS/tmp/x-raw"
EXTRACT_JS="$(cd "$(dirname "$0")" && pwd)/x-extract.js"

TAB=$(mearl tab_open --payload '{"url":"https://x.com/OpenAI","reuse":"prefer"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['tabId'])" 2>/dev/null)
echo "tabId: $TAB"

for user in "$@"; do
  echo -n "📱 @$user ... "
  mearl page_navigate --payload "{\"tabId\":$TAB,\"url\":\"https://x.com/$user\"}" > /dev/null 2>&1
  
  # 轮询等待推文加载（最多 15 秒）
  COUNT=0
  for attempt in 1 2 3 4 5; do
    sleep 3
    COUNT=$(mearl page_eval --payload "{\"tabId\":$TAB,\"expression\":\"document.querySelectorAll('article[data-testid=\\\"tweet\\\"]').length\"}" 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','0'))" 2>/dev/null)
    COUNT=$(echo "$COUNT" | tr -d '"')
    if [ "$COUNT" -ge 3 ] 2>/dev/null; then break; fi
  done
  
  # 提取
  python3 -c "
import json
js = open('$EXTRACT_JS').read()
open('/tmp/x_payload_cur.json','w').write(json.dumps({'tabId': $TAB, 'expression': js}))
"
  RESULT=$(mearl page_eval --payload-file /tmp/x_payload_cur.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','{}'))" 2>/dev/null)
  
  N=$(echo "$RESULT" | python3 -c "import sys,json; d=json.loads(sys.stdin.read()); print(d.get('count',0))" 2>/dev/null)
  
  if [ -z "$N" ] || [ "$N" = "0" ]; then
    echo "❌ 仍为 0 (DOM 文章数: $COUNT)"
  else
    echo "$RESULT" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
out = {'username': '$user', 'scraped_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)', 'total': data.get('count',0), 'tweets': data.get('tweets',[])}
open('$RAW/$user.json','w').write(json.dumps(out, ensure_ascii=False, indent=2))
print(f'✅ {data.get(\"count\",0)} 条')
"
  fi
done
