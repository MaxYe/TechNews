#!/bin/bash
# X.com 批量采集脚本 — 使用 mearl 浏览器登录态
# 用法: bash x-collect.sh [账号1 账号2 ...]   (不传则采配置里全部账号)

WS="/Users/yefan/Works/TechNews/tech-collect"
RAW="$WS/tmp/x-raw"
CONFIG="$WS/config/x-sources.json"
EXTRACT_JS="$(cd "$(dirname "$0")" && pwd)/x-extract.js"
mkdir -p "$RAW"

# 读取账号列表（参数优先，否则读配置）
if [ $# -gt 0 ]; then
  ACCOUNTS=("$@")
else
  ACCOUNTS=($(python3 -c "
import json
cfg = json.load(open('$CONFIG'))
names = []
for tier in ['tier1_official','tier2_key_people','tier3_bonus']:
    for a in cfg['sources'].get(tier, []):
        names.append(a['username'])
print(' '.join(names))
"))
fi

# 打开/复用 x.com 的 tab
TAB=$(mearl tab_open --payload '{"url":"https://x.com/OpenAI","reuse":"prefer"}' 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['tabId'])" 2>/dev/null)
if [ -z "$TAB" ]; then
  echo "❌ 无法打开 tab，检查 mearl 连接"
  exit 1
fi

echo "采集 tabId: $TAB"
echo "待采集账号: ${#ACCOUNTS[@]} 个"
echo "============================================="

OK=0; FAIL=0
for user in "${ACCOUNTS[@]}"; do
  echo -n "📱 @$user ... "
  # 导航
  mearl page_navigate --payload "{\"tabId\":$TAB,\"url\":\"https://x.com/$user\"}" > /dev/null 2>&1
  sleep 3
  
  # 滚动一次加载更多（可选）
  mearl page_scroll --payload "{\"tabId\":$TAB,\"direction\":\"down\",\"amount\":\"viewport\"}" > /dev/null 2>&1
  sleep 2
  
  # 动态生成提取 payload（带正确 tabId）
  python3 -c "
import json
js = open('$EXTRACT_JS').read()
open('/tmp/x_payload_cur.json','w').write(json.dumps({'tabId': $TAB, 'expression': js}))
"
  RESULT=$(mearl page_eval --payload-file /tmp/x_payload_cur.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('result','{}'))" 2>/dev/null)
  
  if [ -z "$RESULT" ] || [ "$RESULT" = "{}" ]; then
    echo "❌ 提取失败"
    FAIL=$((FAIL+1))
    continue
  fi
  
  echo "$RESULT" | python3 -c "
import sys, json
data = json.loads(sys.stdin.read())
out = {'username': '$user', 'scraped_at': '$(date -u +%Y-%m-%dT%H:%M:%SZ)', 'total': data.get('count',0), 'tweets': data.get('tweets',[])}
open('$RAW/$user.json','w').write(json.dumps(out, ensure_ascii=False, indent=2))
print(f'✅ {data.get(\"count\",0)} 条')
"
  OK=$((OK+1))
done

echo "============================================="
echo "完成: 成功 $OK | 失败 $FAIL"
