# 外网技术信息采集（独立版）

> 版本：4.0 | 2026-10-05
> 包含：GitHub Trending 采集 + 外部技术博客采集 + X.com 技术观察采集 + 本地 LLM 预处理
> 特性：完全独立，无外部目录依赖，输出路径由调用者指定；X.com 采集采用 Mearl 复用浏览器登录态；翻译/摘要由本地 Ollama 承担，云端只做趋势分析

---

## 项目结构

```
tech-collect/
├── README.md                    # 本文件
├── run-daily.sh                 # 主编排：串起 Part 1-4
├── config/
│   ├── github-sources.json      # Release 追踪仓库列表、语言分榜
│   ├── blog-sources.json        # 源列表、Tier 分级、RSS 地址
│   └── x-sources.json           # X.com 关注账号列表、采集策略
├── output/                      # 默认输出目录（可覆盖）
│   ├── github/
│   ├── blog/
│   └── x/
└── tmp/                         # 中间文件（7 天后可清理）
    ├── local-prep.py            # Part 0 实现：本地 LLM 翻译/摘要
    ├── llm-out/{DATE}/          # 中文富化 JSON（供云端模型读）
    ├── x-collect.sh             # X.com 采集（Mearl 驱动）
    └── fetch-via-browser.sh     # Cloudflare/登录墙兜底
```

## 执行链路总览

```
run-daily.sh
  Part 1: GitHub Trending 采集   → tmp/raw/{DATE}/github-daily.json
  Part 2: 外部技术博客采集        → tmp/raw/{DATE}/blog-*.xml + anthropic-news.html
  Part 3: X.com 采集（Mearl）     → tmp/x-raw/*.json
  Part 4: 本地 LLM 预处理（Part 0）→ tmp/llm-out/{DATE}/*.json + output/*/{DATE}-local9b.md
  ↓
云端模型：读 tmp/llm-out/{DATE}/*.json（已翻译+已分类）→ 补「跨源趋势分析」→ 产出完整日报
```

---

# Part 0: 本地 LLM 预处理（翻译 / 摘要）

> 目标：把「翻译 + 逐条摘要」这类**确定性文本转换**交给本地 Ollama，不再消耗云端 API 费用。
> 依据：`local-llm-translate-summarize` skill（提示词手册与硬性调用契约以该 skill 为准）。

## 分工原则（三层职责）

| 层 | 负责什么 | 说明 |
|----|----------|------|
| **代码**（Python/shell） | 数据抽取、分类打标、编号、链接、统计 | **能由代码算的，一律不交给模型** |
| **本地 9B**（qwen3.5-32k） | 翻译、逐条摘要、一句话点评 | 确定性文本转换，稳定且零成本 |
| **云端模型** | 跨源趋势分析、跨账号信号观察 | 需全局视野；9B 在信息缺失时幻觉率高，交回云端 |

## 能力边界（先判断该不该用）

| 任务 | 本地 9B | 说明 |
|------|:---:|------|
| 翻译 | ✅ 很好 | 质量接近云端 |
| 逐条摘要 | ✅ 很好 | 100–200 字摘要稳定 |
| 分类打标 | ⚠️ 用代码做 | 模型会输出空标签，关键词匹配更可靠 |
| 数字 / 统计 | ❌ 不要用 | 直接代码计算 |
| 跨源趋势分析 | ❌ **交回云端** | 需要全局视野，9B 幻觉率高 |

## 数据流

```
原始数据（tmp/raw + tmp/x-raw）
   ↓ 代码：抽取 + 分类打标（关键词匹配）
items.json
   ↓ 本地 9B：批量翻译 / 摘要（skill helper）
结果 JSON
   ↓ 代码：合并 + 组装结构（编号、标签、链接、互动数据）
   ├→ ① tmp/llm-out/{DATE}/{github,blog,x}.json   ← 中文富化 JSON，供云端模型读
   └→ ② output/{github,blog,x}/{DATE}-local9b.md  ← 报告骨架，供人快速浏览
```

**关键收益**：云端模型不再读 4.4MB 原始英文数据，只需读 **46KB 中文富化 JSON（约 1%）**，且逐条翻译已由本地完成。

## 硬性调用契约（配错就跑不起来）

| 参数 | 必须值 | 原因 |
|------|--------|------|
| `think` | **`false`** | ⚠️ 最关键。默认 `true` 会生成超长思维链，实测 **>7 分钟无响应**（像卡死） |
| `num_ctx` | `32768` | 模型原生 256K，但 **Ollama 默认仅 4096**，稍长文本即被截断 |
| `temperature` | `0.3` | 默认 1.0 输出发散、术语不一致 |
| `top_p` | `0.9` | 略收窄采样 |
| `num_predict` | 1200 起 | 显式限制，防输出失控 |

端点：`http://localhost:11434/api/generate`；模型：`qwen3.5-32k`（已带 `num_ctx=32768`）。

## 核心设计：模型只出 JSON，结构由代码组装

让模型直接输出完整 Markdown 会导致**编号错乱、格式漂移、token 翻倍**。
改为「模型出 JSON → 代码拼结构」后：**格式零漂移、速度快一倍、token 省一半**。

批量提示词要点：
- 要求「严格只输出 JSON 数组，不要任何其他文字」
- 字段固定（如 `[{"i":0,"title_cn":"...","summary":"..."}]`）
- 明确「`i` 是列表序号，必须原样返回」
- 每批 **4–6 条**（过多会超上下文 / 质量下降）
- 编号、标签、链接、统计**全部由代码负责**

## 防幻觉三原则（9B 必须加）

实测 9B 在数据缺失时会**编造内容**（例：仓库无描述，模型自行编了「专注于 TypeScript 生态的代码生成工具」）。

| 原则 | 做法 |
|------|------|
| ① 显式声明缺失 | 输入里标注「（数据源无描述）」，并指令「必须输出『（无描述）』」 |
| ② 禁止推测 | 明确写出「不得推测」「严禁编造」 |
| ③ 代码侧兜底 | 分类标签、数字统计、链接、互动数据全部代码生成 |

## 容错：漏条补齐重试

9B 批量输出 JSON 时会**随机漏条目**（实测出现某条未返回 `title_cn`/`summary`）。
`local-prep.py` 的 `call_skill_with_fill()` 会：
1. 逐条检查必需字段是否齐全
2. 缺失的条目**用更小批次重试**（最多 3 轮，批次逐步缩小到 1）
3. 仍缺失则用占位符标记，并在报告中可见

## 执行方式

```bash
# 独立执行
python3 tmp/local-prep.py ping                    # 连通性检查
python3 tmp/local-prep.py github --date YYYY-MM-DD
python3 tmp/local-prep.py blog   --date YYYY-MM-DD
python3 tmp/local-prep.py x      --date YYYY-MM-DD
python3 tmp/local-prep.py all    --date YYYY-MM-DD

# 随每日任务自动执行（run-daily.sh Part 4）
ENABLE_LOCAL_LLM=1 bash run-daily.sh   # 默认开；ollama/模型不可用则自动跳过（云端兜底）
ENABLE_LOCAL_LLM=0 bash run-daily.sh   # 关闭本地预处理，回到纯云端方案
```

## 实测数据（3 份报告：16 仓库 + 26 文章 + 14 推文）

| 项 | 数值 |
|----|------|
| 本地 token 消耗 | 输入 5,514 + 输出 4,155 = **9,669 tokens** |
| 调用次数 | 13+ 次 |
| 耗时 | 约 **6.5 分钟** |
| 云端费用 | **0** |
| 占云端总工作量 | 约 **18.5%**（保守估计，博客原始数据超出测量上限） |

## 适用边界提醒

- 本地预处理**失败不阻塞**采集：ollama/模型不可用时自动跳过，云端照常产出（只是费用回升）
- 趋势分析、跨源综合判断**始终交回云端**——这是 Part 0 刻意不覆盖的部分

---

# Part 1: GitHub Trending 采集

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `{DATE}` | 是 | 今天 | 采集日期，YYYY-MM-DD |
| `{OUTPUT_PATH}` | 是 | — | 输出文件完整路径 |
| `{TOPICS}` | 否 | AI Coding, Agent, MCP, Harness, LLM | 专题关键词 |
| `{TMP_DIR}` | 否 | ./tmp | 中间文件目录 |
| `{PREV_REPORT}` | 否 | — | 前一天报告路径，用于对比连续性 |

## 前置条件

- 无需登录
- 需网络访问 github.com 和 api.github.com
- 请求间隔 1-2 秒避免 rate limit

## 采集流程

### Step 1: Daily Trending 总榜

```bash
curl -sL -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" \
  "https://github.com/trending?since=daily"
```

解析 HTML 中 `article.Box-row` 元素，提取：owner/repo、描述、编程语言、总 Star、今日 Star 增量、链接。

总榜有时仅返回 20 个仓库（页面真实行为），差额从语言分榜按增量排序补齐到 25。

### Step 2: 语言分榜

依次抓取（间隔 1-2s）：
- https://github.com/trending/python?since=daily
- https://github.com/trending/typescript?since=daily
- https://github.com/trending/javascript?since=daily

用途：补齐总榜差额 + 发现未进总榜但专题相关的项目。

### Step 3: Weekly Trending

```bash
curl -sL -A "..." "https://github.com/trending?since=weekly"
```

取前 25，用于「本周趋势观察」区块，标注连续在榜和爆发型项目。

### Step 4: 生态 Release 追踪（GitHub REST API）

```bash
REPOS=(
  "anthropics/claude-code"
  "openai/codex"
  "crewAIInc/crewAI"
  "langchain-ai/langgraph"
  "modelcontextprotocol/servers"
  "continuedev/continue"
  "microsoft/autogen"
  "Aider-AI/aider"
)

for repo in "${REPOS[@]}"; do
  curl -sL "https://api.github.com/repos/$repo/releases?per_page=2"
  curl -sL "https://api.github.com/repos/$repo"
  sleep 1.5
done
```

提取：最新版本号 + 发布日期 + 总 Star + Release 要点。

未认证 API 限额 60/h，若接近限额则跳过此步，报告中注明。

### Step 5: 数据整合

1. 合并 Daily + 语言分榜补充 + Weekly 精选
2. 以 `owner/repo` 为 key 去重，保留最完整字段
3. 若有 `{PREV_REPORT}`，对比标注「连续 N 日在榜」「增量较昨日 ×N」「新上榜」

### Step 6: 专题分类

| 专题 | 匹配关键词 |
|------|-----------|
| AI Coding | code/coding/IDE/editor/copilot/cursor/claude code/codex/aider/continue/windsurf |
| Agent 框架 | agent/multi-agent/ReAct/tool calling/autonomous/swarm |
| Agent Harness/Skills | harness/skill/MCP/memory/context/observ |
| MCP 协议 | MCP/model context protocol/server |
| LLM 基础设施 | inference/transformer/fine-tune/RAG/embedding/vector |

一个仓库可多标签。

### Step 7: 趋势洞察

基于全量数据撰写 3-6 条核心趋势观察：跨仓库主题聚合、与前日对比拐点、中国团队表现、生态信号（高频发版 vs 静默）。

## 容错策略

| 异常 | 处理 |
|------|------|
| curl 返回空/403 | 改用 WebFetch 重试（最多 3 次） |
| 安全网关拦截 | WebFetch → WebSearch 间接获取 |
| API rate limit | 跳过 Release 追踪，报告注明 |
| 总榜不足 25 | 从语言分榜按增量补齐，标注来源 |
| 某分榜失败 | 不影响主体，报告注明 |
| 全部网络不可达 | 报告写入「采集失败」+ 原因 |

## 输出格式

```markdown
# GitHub Trending - {DATE}

> 采集时间：{DATE}
> 来源：https://github.com/trending（daily/weekly 总榜 + 语言分榜）+ GitHub REST API
> 采集方式：{实际方法}

## 当日 Trending Top 25

### 1. owner/repo
- 描述：{英文原文}（{中文翻译}）
- 语言：{language}
- 今日 Star：+{N} ｜ 总 Star：{N}
- 今日亮点：{一句话点评，含与昨日对比}
- 分类标签：`{tag1}` `{tag2}`
- 链接：https://github.com/owner/repo

（重复至 25）

## 本周 Trending 观察（Weekly 精选 Top 10）

> {一句话概括本周主题}

### 1. owner/repo
- 描述：...
- 语言：{lang} ｜ 本周 Star：+{N} ｜ 总 Star：{N}
- 本周亮点：...
- 分类标签：...
- 链接：...

## 生态 Release 动态

### {owner/repo} — {version}（{date}）
- Star：{N} ｜ 语言：{lang}
- 要点：{2-3 句核心变化}
- 链接：https://github.com/{owner}/{repo}/releases

## 专题关注

### AI Coding 相关
- {owner/repo}（+{N} today / +{N} week）— {一句话}

### Agent Harness / Skills 生态
- ...

### Agent 框架相关
- ...

### MCP 协议相关
- ...

### LLM 应用 / 本地化基础设施
- ...

## 采集统计

- 总采集仓库数：{N}（去重后；来源明细）
- 专题相关仓库数：{N}（按专题分布）
- 今日 Star 增长 Top 5：
  1. {owner/repo} — +{N}
- 本周 Star 增长 Top 5：
  1. {owner/repo} — +{N}

## 采集异常说明

{无异常写「无异常」；有异常列表说明站点/状态/原因/降级措施}

## 今日核心趋势观察

1. **{标题}**：{2-3 句分析}
（3-6 条）
```

## 质量底线

- 总条目 ≥ 20（正常 40+）
- 每条必含：仓库名 + 描述 + Star 数 + 链接
- 所有数据为真实抓取，不编造
- 专题分类覆盖全部 `{TOPICS}`
- 异常均有记录

## 与 Part 0 的分工

| 环节 | 由谁完成 |
|------|----------|
| 仓库名、语言、Star、链接、分类标签 | **代码**（直取/关键词匹配） |
| 描述翻译、一句话亮点 | **本地 9B**（Part 0，产出 `output/github/{DATE}-local9b.md`） |
| Weekly 精选、Release 动态、趋势洞察 | **云端模型** |

> 云端模型可直接读 `tmp/llm-out/{DATE}/github.json`（已含 `translation`/`highlight`/`tags`），无需重做翻译。

## 调用示例

```
请按照 GitHub Trending 采集流程执行：
- DATE: 2026-09-16
- OUTPUT_PATH: ./output/github/2026-09-16.md
- TOPICS: AI Coding, Agent, MCP, Harness
- TMP_DIR: ./tmp
- PREV_REPORT: ./output/github/2026-09-15.md
```

---

# Part 2: 外部技术博客采集

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `{DATE}` | 是 | 今天 | 采集日期，YYYY-MM-DD |
| `{OUTPUT_PATH}` | 是 | — | 输出文件完整路径 |
| `{TOPICS}` | 否 | AI Coding, Agent, Harness, Tech Trends | 分类标签 |
| `{TMP_DIR}` | 否 | ./tmp | 中间文件目录 |
| `{PREV_REPORTS}` | 否 | — | 前 1-3 天报告路径，用于去重 |
| `{TIME_WINDOW}` | 否 | 3 | 取最近 N 天内的文章 |

## 前置条件

- 无需登录（全部公开站点）
- 需外网访问能力
- 部分站点在公司网络下被安全网关拦截，已有降级策略覆盖

## 源列表

### Tier 1: 核心源（必采，失败需注明）

| # | 站点 | URL | 采集方式 |
|---|------|-----|----------|
| 1 | Google AI Blog | https://blog.google/technology/ai/ | RSS: `https://blog.google/technology/ai/rss/` |
| 2 | OpenAI Blog | https://openai.com/news/ | RSS: `https://openai.com/news/rss.xml`（HTML 常有 Cloudflare 挑战） |
| 3 | Anthropic News | https://www.anthropic.com/news | WebFetch → curl（常被拦截，准备降级） |
| 4 | Hacker News | https://news.ycombinator.com/ | Algolia API: `http://hn.algolia.com/api/v1/search_by_date?tags=front_page&hitsPerPage=30` |
| 5 | Simon Willison | https://simonwillison.net/ | RSS: `https://simonwillison.net/atom/everything/` |
| 6 | InfoQ 中国 | https://www.infoq.cn/ | WebFetch / 解析 `__NUXT_DATA__` |

### Tier 2: 扩展源（尝试采集，失败可跳过）

| # | 站点 | URL | 采集方式 |
|---|------|-----|----------|
| 7 | Latent Space | https://www.latent.space/ | RSS: `https://www.latent.space/feed` |
| 8 | Meta AI Blog | https://ai.meta.com/blog/ | WebFetch |
| 9 | HuggingFace Blog | https://huggingface.co/blog | WebFetch |
| 10 | Microsoft AI Blog | https://blogs.microsoft.com/ai/ | RSS: `https://blogs.microsoft.com/ai/feed/` |
| 11 | Eugene Yan | https://eugeneyan.com/ | RSS: `https://eugeneyan.com/rss/` |
| 12 | Chip Huyen | https://huyenchip.com/ | RSS: `https://huyenchip.com/feed.xml` |

### Tier 3: 中文补充源

| # | 站点 | URL |
|---|------|-----|
| 13 | 机器之心 | https://www.jiqizhixin.com/ |
| 14 | 量子位 | https://www.qbitai.com/ |

## 采集流程

### Step 1: 多策略抓取

对每个源按优先级尝试：

```
策略 A: RSS/Atom Feed（最可靠）
  → curl -sL "{RSS_URL}" → 解析 XML <item>/<entry>

策略 B: WebFetch 工具
  → 提示词：「列出最近 {TIME_WINDOW} 天内发布的技术文章，
     提取：标题、作者、发布日期、链接、摘要(100-200字)」

策略 C: curl + User-Agent
  → curl -sL -A "Mozilla/5.0 ..." "{URL}" → 解析 HTML

策略 D: 专用 API
  → Hacker News: hn.algolia.com
  → InfoQ: __NUXT_DATA__ 或 XHR JSON

策略 E: WebSearch 间接覆盖
  → 搜索 "site:{domain} {DATE}" 确认文章存在
```

### Step 2: Hacker News 专项

```bash
curl -s "http://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=30&numericFilters=created_at_i>$(date -v-{TIME_WINDOW}d +%s)"
```

筛选：points ≥ 50 或 comments ≥ 30，且与 `{TOPICS}` 相关。

### Step 3: 内容结构化

每篇文章提取：
- 标题（原文 + 中文翻译）
- 来源站点名
- 作者
- 发布日期
- 链接（文章原始 URL）
- 摘要（100-200 字中文提炼，非原文复制）
- 分类标签（1-2 个）

### Step 4: 去重

若提供 `{PREV_REPORTS}`：读取前 1-3 天报告，提取已收录链接，跳过重复文章。

### Step 5: 专题分类

| 标签 | 匹配规则 |
|------|----------|
| AI Coding | 代码生成/IDE/编程助手/Code Review/Copilot/Claude Code/Cursor/Codex |
| Agent | 智能体/自主任务/tool calling/多 Agent/Agent 安全 |
| Harness | 工程护栏/验证闭环/可观测性/评估/状态管理/容错 |
| Tech Trends | 模型发布/行业动态/融资收购/监管政策/开源生态 |

### Step 6: 跨源趋势分析

撰写 3-5 条「跨源核心趋势」：多源同时报道的主题、社区 vs 官方叙事差异、趋势延续或拐点、对工程实践有指导意义的洞察。

## 容错策略

| 异常 | 识别方式 | 处理 |
|------|----------|------|
| 安全网关拦截 | 响应含 `office-sec` / `data-security-manager` | 尝试 RSS → 浏览器兜底 → 最终跳过并标注 ❌ |
| Cloudflare 挑战 | 403 + `Just a moment...` / `challenges.cloudflare.com` | **浏览器兜底**（curl 无法通过，即使 RSS 地址也受挑战）|
| 连接超时/重置 | HTTP 000 / Connection reset | 重试 3 次（间隔 2s），仍失败 → 浏览器兜底 |
| RSS 地址已废弃 | 返回「archived or suspended」/ 404 | 从站点页面重新发现 feed 地址并更新配置 |
| 站点可达但无新内容 | 最新文章 > `{TIME_WINDOW}` 天前 | 标注 ⚠️，不计入正文 |
| RSS 返回空 | XML 解析失败 / 0 items | 降级到 WebFetch |
| Tier 1 有 3+ 源失败 | — | 报告标注覆盖不足 |

### 浏览器兜底（突破 Cloudflare / 登录墙）

当 curl 非 200 **或** 返回内容不是合法 RSS/Atom（Cloudflare 拦截页可能是 200/403 且体积不小）时，自动改用 mearl 浏览器抓取：

```bash
bash tmp/fetch-via-browser.sh "<feed_url>" "<out_file>"
```

原理：先在同源页面打开跳板，再在页面上下文内执行 `fetch()`，自动携带浏览器会话 Cookie，可穿透 Cloudflare 托管挑战与登录墙。

> **实测案例**：Microsoft AI Blog 旧地址 `blogs.microsoft.com/ai/` 已废弃并迁移至 `news.microsoft.com/source/topics/ai/`，新 feed 有 Cloudflare 挑战（curl 403），浏览器兜底后成功获取 19.5KB / 10 条。

**间接覆盖策略**：Anthropic 被拦截时通过 Simon Willison / HN / InfoQ 间接覆盖；Meta/HuggingFace 被拦截时通过 HN / 中文媒体覆盖（Microsoft 已通过浏览器兜底直采）。

## 输出格式

```markdown
# 外部技术博客 - {DATE}

> 采集时间：{DATE}
> 来源：{成功采集的源列表}
> 说明：{失败/跳过的源概述}
> 分类标签：{TOPICS}

## {站点名 1}

### 1. {标题}（{中文翻译}）
- 来源：{站点名}
- 作者：{author}
- 日期：{YYYY-MM-DD}
- 链接：{URL}
- 摘要：{100-200字中文}
- 分类：{Tag1} / {Tag2}

### 2. ...

## {站点名 2}
...

## 异常说明

| 站点 | 状态 | 原因 |
|------|------|------|
| {URL} | ❌ 未采集 | {原因} |
| {URL} | ⚠️ 无新内容 | 最新为 {date} |

**降级策略**：{如何通过其他源间接覆盖}

## 统计

- 总采集文章数：{N} 篇
  - {站点}：{N} 篇
- 主题分布：
  - AI Coding：{N} 篇
  - Agent：{N} 篇
  - Harness：{N} 篇
  - Tech Trends：{N} 篇
- 时效性：{N} 篇当日，{N} 篇前 1-3 天

## 今日跨源核心趋势

1. **{标题}**：{分析，引用具体文章编号}
（3-5 条）
```

## 质量底线

- 总条目 ≥ 10（正常 15-25 篇）
- Tier 1 至少成功 4/6
- 每条必含：标题 + 链接 + 摘要 + 日期
- 链接真实可访问，不编造
- 摘要为中文提炼
- 异常源全部记录

## 与 Part 0 的分工

| 环节 | 由谁完成 |
|------|----------|
| 来源、日期、链接、分类标签 | **代码** |
| 标题中文化、100–200 字摘要 | **本地 9B**（Part 0，产出 `output/blog/{DATE}-local9b.md`） |
| 主题分布统计、跨源核心趋势 | **云端模型** |

> 云端模型可直接读 `tmp/llm-out/{DATE}/blog.json`（已含 `title_cn`/`summary`/`tags`），无需重做摘要。

## 调用示例

```
请按照外部技术博客采集流程执行：
- DATE: 2026-09-16
- OUTPUT_PATH: ./output/blog/2026-09-16.md
- TOPICS: AI Coding, Agent, Harness, Tech Trends
- TMP_DIR: ./tmp
- TIME_WINDOW: 3
- PREV_REPORTS: ./output/blog/2026-09-15.md
```

---

# Part 3: X.com 技术观察采集

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `{DATE}` | 是 | 今天 | 采集日期，YYYY-MM-DD |
| `{OUTPUT_PATH}` | 是 | — | 输出文件完整路径 |
| `{TOPICS}` | 否 | AI Coding, Agent, Harness, Tech Trends, Research | 分类标签 |
| `{TMP_DIR}` | 否 | ./tmp | 中间文件目录 |
| `{PREV_REPORT}` | 否 | — | 前一天报告路径，用于去重 |
| `{TIME_WINDOW}` | 否 | 3 | 取最近 N 天内的推文 |
| `{TOP_N_PER_ACCOUNT}` | 否 | 5 | 每个账号最多选取的推文数 |
| `{MIN_ENGAGEMENT}` | 否 | likes≥50, retweets≥10 | 最低互动门槛 |

## 前置条件

- 本地 Chrome 已登录 X.com 账号（复用登录态，不需要 API Key）
- 已安装 Mearl CLI 与浏览器扩展：`npx @mearl/setup --yes`（本机控制模式）
- Mearl 通过本地扩展 / Unix Socket 连接 Chrome，`mearl browser_list` 应返回 `connected`
- 首次访问 x.com 需授权域名：`mearl request_domain_permission --payload '{"domain":"x.com"}'`
- **关键背景**：公司网络下命令行工具直连 X.com / Nitter / RSSHub 全部返回 HTTP 000（网关拦截），但浏览器走代理可以正常访问；Mearl 正是通过复用浏览器登录态绕开这层封锁

## 采集策略（按优先级自动降级）

```
策略 A: Mearl 浏览器复用（首选，已验证可用）
  → mearl page_navigate 打开 https://x.com/{username}
  → mearl page_scroll 触发无限滚动加载
  → mearl page_eval 执行 JS 从页面 DOM 抽取推文（含全文/日期/链接/互动数据）
  → 优点：复用登录态、直连真实 X 页面、数据 100% 真实、无需任何 API

策略 B: X API v2（付费，$100/月 Basic Tier）
  → GET /2/users/:id/tweets?tweet.fields=public_metrics,created_at&max_results=10
  → 数据结构化、合规、可靠；每月 10,000 条推文额度
  → 适用于无浏览器环境（CI / 远端服务器）

策略 C: WebSearch 间接覆盖（降级，覆盖率低）
  → 搜索 "from:@{username} {topic}"
  → 只能获取被搜索引擎索引的热门推文

策略 D: 标注失败
  → 记录失败账号、原因、尝试过的策略
```

## 源列表

详见 `config/x-sources.json`，按 Tier 分级：

### Tier 1: 官方账号（必采，失败需注明）

| # | 账号 | 名称 | 类别 |
|---|------|------|------|
| 1 | @OpenAI | OpenAI | OpenAI 官方 |
| 2 | @GoogleDeepMind | Google DeepMind | Google DeepMind 官方 |
| 3 | @AnthropicAI | Anthropic | Anthropic 官方 |
| 4 | @claudeai | Claude | Claude 产品官方 |

### Tier 2: 关键人物（尽力采集，汇总报告）

| 类别 | 账号 | 角色 |
|------|------|------|
| OpenAI | @sama | Sam Altman, CEO |
| OpenAI | @gdb | Greg Brockman, President |
| OpenAI | @markchen90 | Mark Chen, CRO |
| OpenAI | @merettm | Jakub Pachocki, Chief Scientist |
| OpenAI | @polynoamial | Noam Brown, 推理/o系列 |
| OpenAI | @NoamShazeer | Noam Shazeer, 架构研究 |
| OpenAI | @thsottiaux | Thibault Sottiaux, Codex 工程 |
| Anthropic | @DarioAmodei | Dario Amodei, CEO |
| Anthropic | @ch402 | Chris Olah, 可解释性 |
| Anthropic | @jackclarkSF | Jack Clark, 政策/安全 |
| Anthropic | @bcherny | Boris Cherny, Claude Code 创建者 |
| Anthropic | @alexalbert__ | Alex Albert, 开发者关系 |
| Anthropic | @AmandaAskell | Amanda Askell, Alignment |
| Anthropic | @DanielaAmodei | Daniela Amodei, President |

### Tier 3: 补充关注

| 类别 | 账号 | 角色 |
|------|------|------|
| 中文区 | @dotey | 宝玉, AI Engineer |
| 额外 | @karpathy | Andrej Karpathy, 前OpenAI |
| 额外 | @ilyasut | Ilya Sutskever, 前OpenAI/SSI |

## 采集流程

### Step 1: 批量抓取（Mearl 自动化）

对每个账号执行「导航 → 滚动加载 → JS 抽取」三步（已封装为 `tmp/x-collect.sh` 一键执行）：

```bash
# 0. 确认浏览器连接（应返回 connected）
mearl browser_list

# 1. 打开/复用 X.com 标签页（首次需先 request_domain_permission 授权）
mearl tab_open --payload '{"url":"https://x.com/OpenAI","reuse":"prefer"}'
# → 记录返回的 tabId

# 2. 导航到目标账号主页
mearl page_navigate --payload '{"tabId":{TAB},"url":"https://x.com/{username}"}'

# 3. 滚动触发无限滚动加载（视口向下滚 1-2 次）
mearl page_scroll --payload '{"tabId":{TAB},"direction":"down","amount":"viewport"}'

# 4. page_eval 执行 JS 抽取推文
mearl page_eval --payload '{"tabId":{TAB},"expression":"<抽取JS表达式>"}'
```

**抽取 JS 核心逻辑**（`page_eval` 的 `expression`）：遍历 `article[data-testid="tweet"]`，从每个 article 提取 ——

| 字段 | 来源 |
|------|------|
| tweet_id / 链接 | `a[href*="/status/"]` 的 href |
| 正文 | `[data-testid="tweetText"]` 的 innerText |
| 日期 | `time` 的 datetime 属性 |
| 回复数 | `[data-testid="reply"]` 的 aria-label 首个数字 |
| 转帖数 | `[data-testid="retweet"]` 的 aria-label 首个数字 |
| 喜欢数 | `[data-testid="like"]` 的 aria-label 首个数字 |
| 书签数 | aria-label 含「书签」片段中的数字 |
| 浏览数 | `a[aria-label*="查看"]` 的 aria-label 首个数字 |

> 参考实现见 `tech-collect/tmp/x-collect.sh`（含完整抽取 JS 与批量循环）。

账号间导航间隔 3 秒；页面加载慢导致抽到 0 条时，用「轮询等待」：导航后每 3 秒查一次 `document.querySelectorAll('article[data-testid="tweet"]').length`，≥ 3 条再抽取，最多等 15 秒。

**一键批量采集**（读取 `config/x-sources.json` 的全部账号）：

```bash
cd tech-collect/tmp && bash x-collect.sh
# 可选参数：bash x-collect.sh OpenAI sama dotey   （只采指定账号）
```

### Step 2: 内容筛选

对每个账号的推文：
1. 过滤：排除转推（retweet）、回复（reply），仅保留原创
2. 时间：基于 `{TIME_WINDOW}` 过滤（默认 3 天内）
3. 互动门槛：likes ≥ `{MIN_ENGAGEMENT.likes}` 或 retweets ≥ `{MIN_ENGAGEMENT.retweets}`
4. 专题匹配：按 `{TOPICS}` 关键词库分类
5. 排序：按互动总量（likes + retweets * 2 + replies * 3）降序
6. 截断：每账号最多取 `{TOP_N_PER_ACCOUNT}` 条

### Step 3: 内容结构化

每条推文提取：
- 账号（@username + Display Name）
- 所属类别（OpenAI / Anthropic / Google DeepMind / 中文区 / 其他）
- 发布日期
- 推文内容（英文原文 + 中文摘要翻译）
- 链接：`https://x.com/{username}/status/{tweet_id}`
- 互动数据：❤️{N} 🔄{N} 💬{N}
- 分类标签（1-2 个）
- 点评（一句话，分析信号意义）

### Step 4: 去重

- 若提供 `{PREV_REPORT}`：读取前一天报告中的推文链接，跳过已收录
- 同一天内同一主题的推文，取互动最高的
- 官方账号与个人账号重复报道同一事件，保留最权威来源

### Step 5: 跨账号信号聚合

在报告末尾撰写 3-5 条「跨账号信号观察」：
- 多账号同时在讨论的主题
- 官方 vs. 个人的叙事差异
- 隐含的竞争动态（如一家密集发版时另一家静默）
- 对开发者和工程实践有指导意义的信号

## 容错策略

| 异常 | 识别方式 | 处理 |
|------|----------|------|
| Mearl 未连接浏览器 | `browser_list` 返回 connectedCount 0 | 运行 `mearl check` 排查；确保 Chrome 扩展已启用 |
| x.com 未授权 | `tab_open` 报 "not authorized" | 先 `request_domain_permission` 授权后重试 |
| 页面未加载完（抽到 0 条） | `page_eval` 结果 count=0 | 轮询等待：每 3s 查 article 数量，≥3 再抽取，最多 15s |
| 无限滚动未触发新内容 | 滚动后推文数不变 | 已抽到当前视口推文即可；必要时多滚动 1-2 次 |
| tabId 失效/过期 | action 报 tab 不存在 | 重新 `tab_open` 获取新 tabId |
| 账号无近期推文 | 最新推文日期 > `{TIME_WINDOW}` 天 | 标注 ⚠️ 长期静默，仍记录最新一条供参考 |
| 推文互动 < 门槛 | 互动量均不达标 | 标注 ⚠️ 低活跃，不计入正文 |
| 同一事件多账号报道 | 内容高度相似 | 保留互动最高一条，其余标注参见 |
| Mearl 整体不可用 | CLI / 扩展异常 | 降级到 X API v2（若有 key）→ WebSearch → 标注失败 |

**间接覆盖策略**：关键人物（如 @DarioAmodei、@sama）若无法直接采集，通过其所在公司官方账号 @AnthropicAI / @OpenAI 内容间接覆盖；@DanielaAmodei、@ch402 等长期静默账号，报告中标注「最近发言停留在 {date}」。

## 输出格式

```markdown
# X.com 技术观察 - {DATE}

> 采集时间：{DATE}
> 关注账号：{N} 个（Tier 1: {n1} | Tier 2: {n2} | Tier 3: {n3}）
> 成功采集：{N} 个 | 失败：{N} 个
> 采集方式：{实际使用的策略}
> 分类标签：{TOPICS}

## 🔥 今日信号热点

> {3-5 条一句话热点摘要，标注信号强度和来源账号}

## OpenAI 生态

### @OpenAI（官方）
（若无可标注「本期无高互动推文」）

#### 1. {推文内容摘要 / 中文翻译}
- 日期：YYYY-MM-DD
- 链接：https://x.com/OpenAI/status/{id}
- 互动：❤️{N} 🔄{N} 💬{N}
- 标签：`{tag1}` `{tag2}`
- 点评：{一句话分析}

### @sama — Sam Altman（CEO）
...

### @polynoamial — Noam Brown（推理）
...

## Anthropic 生态

### @AnthropicAI（官方）
...

### @DarioAmodei — Dario Amodei（CEO）
...

### @bcherny — Boris Cherny（Claude Code）
...

## Google DeepMind

### @GoogleDeepMind（官方）
...

## 中文区

### @dotey — 宝玉（AI Engineer）
...

## 额外关注

### @karpathy — Andrej Karpathy
...

## 异常说明

| 账号 | 状态 | 尝试策略 | 原因 |
|------|------|----------|------|
| @xxx | ❌ 未采集 | mearl → 降级链 | 浏览器未连接/页面加载失败 |
| @yyy | ⚠️ 长期静默 | mearl | 最近发言停留在 {date} |

## 统计

- 总采集账号数：{N} | 成功：{N} | 失败：{N}
- 总收录推文数：{N} 条
  - OpenAI 生态：{N} 条（{n} 个账号）
  - Anthropic 生态：{N} 条（{n} 个账号）
  - Google DeepMind：{N} 条
  - 中文区：{N} 条
  - 额外补充：{N} 条
- 主题分布：
  - AI Coding：{N} 条
  - Agent：{N} 条
  - Harness：{N} 条
  - Tech Trends：{N} 条
  - Research：{N} 条
- 互动量 Top 5 推文：
  1. @{username} — ❤️{N} 🔄{N} — {摘要}

## 跨账号信号观察

1. **{信号标题}**：{分析，引用具体推文}（2-3 句）
（3-5 条）
```

## 质量底线

- Tier 1 官方账号至少成功 3/4
- 总收录推文 ≥ 10 条
- 每条必含：账号 + 内容摘要 + 链接 + 互动数据 + 日期
- 所有推文链接真实可访问，内容为真实抓取
- 分类标签覆盖全部 `{TOPICS}`
- 异常账号全部记录在异常说明中

## 与 Part 0 的分工

| 环节 | 由谁完成 |
|------|----------|
| 账号、日期、链接、**互动数据（❤️🔄💬👁）**、分类标签 | **代码**（互动数据必须直取，不可由模型生成） |
| 推文翻译、一句话点评 | **本地 9B**（Part 0，产出 `output/x/{DATE}-local9b.md`） |
| 跨账号信号观察、叙事差异分析 | **云端模型** |

> 云端模型可直接读 `tmp/llm-out/{DATE}/x.json`（已含 `translation`/`highlight`/`tags`，互动数据由代码保证真实）。

## 调用示例

```
请按照 X.com 技术观察采集流程执行：
- DATE: 2026-10-03
- OUTPUT_PATH: ./output/x/2026-10-03.md
- TOPICS: AI Coding, Agent, Harness, Tech Trends, Research
- TMP_DIR: ./tmp
- TIME_WINDOW: 3
- TOP_N_PER_ACCOUNT: 5
- COLLECT_METHOD: mearl  # 复用浏览器登录态
```

一键采集（读取 config/x-sources.json 全部账号）：

```bash
cd tech-collect/tmp
bash x-collect.sh                    # 采集全部 21 个账号
bash x-collect.sh OpenAI sama dotey  # 或只采指定账号
bash x-collect-retry.sh <账号...>     # 补采页面加载慢的账号（带轮询等待）
```

采集后数据落在 `tmp/x-raw/{username}.json`，再据此撰写报告（结构见「输出格式」）。

---

# 附录：配置文件模板

## config/github-sources.json

```json
{
  "trending_pages": {
    "daily": "https://github.com/trending?since=daily",
    "weekly": "https://github.com/trending?since=weekly",
    "languages": ["python", "typescript", "javascript"]
  },
  "release_tracking": [
    "anthropics/claude-code",
    "openai/codex",
    "crewAIInc/crewAI",
    "langchain-ai/langgraph",
    "modelcontextprotocol/servers",
    "continuedev/continue",
    "microsoft/autogen",
    "Aider-AI/aider"
  ],
  "topic_keywords": {
    "AI Coding": ["code", "coding", "IDE", "editor", "copilot", "cursor", "claude code", "codex", "aider", "continue", "windsurf"],
    "Agent 框架": ["agent", "multi-agent", "ReAct", "tool calling", "autonomous", "swarm"],
    "Agent Harness/Skills": ["harness", "skill", "MCP", "memory", "context", "observ"],
    "MCP 协议": ["MCP", "model context protocol", "server"],
    "LLM 基础设施": ["inference", "transformer", "fine-tune", "RAG", "embedding", "vector"]
  }
}
```

## config/blog-sources.json

```json
{
  "tier1": [
    {"name": "Google AI Blog", "url": "https://blog.google/technology/ai/", "rss": "https://blog.google/technology/ai/rss/", "method": "rss"},
    {"name": "OpenAI Blog", "url": "https://openai.com/news/", "rss": "https://openai.com/news/rss.xml", "method": "rss"},
    {"name": "Anthropic News", "url": "https://www.anthropic.com/news/", "rss": null, "method": "webfetch"},
    {"name": "Hacker News", "url": "https://news.ycombinator.com/", "rss": null, "method": "algolia_api", "api": "http://hn.algolia.com/api/v1/search_by_date?tags=front_page&hitsPerPage=30"},
    {"name": "Simon Willison", "url": "https://simonwillison.net/", "rss": "https://simonwillison.net/atom/everything/", "method": "rss"},
    {"name": "InfoQ 中国", "url": "https://www.infoq.cn/", "rss": null, "method": "webfetch"}
  ],
  "tier2": [
    {"name": "Latent Space", "url": "https://www.latent.space/", "rss": "https://www.latent.space/feed", "method": "rss"},
    {"name": "Meta AI Blog", "url": "https://ai.meta.com/blog/", "rss": null, "method": "webfetch"},
    {"name": "HuggingFace Blog", "url": "https://huggingface.co/blog", "rss": null, "method": "webfetch"},
    {"name": "Microsoft AI Blog", "url": "https://blogs.microsoft.com/ai/", "rss": "https://blogs.microsoft.com/ai/feed/", "method": "rss"},
    {"name": "Eugene Yan", "url": "https://eugeneyan.com/", "rss": "https://eugeneyan.com/rss/", "method": "rss"},
    {"name": "Chip Huyen", "url": "https://huyenchip.com/", "rss": "https://huyenchip.com/feed.xml", "method": "rss"}
  ],
  "tier3": [
    {"name": "机器之心", "url": "https://www.jiqizhixin.com/", "rss": null, "method": "webfetch"},
    {"name": "量子位", "url": "https://www.qbitai.com/", "rss": "https://www.qbitai.com/feed", "method": "rss"}
  ],
  "known_blocked": ["anthropic.com", "ai.meta.com", "huggingface.co/blog", "blogs.microsoft.com/ai"],
  "topic_keywords": {
    "AI Coding": ["code generation", "IDE", "coding assistant", "code review", "copilot", "claude code", "cursor", "codex"],
    "Agent": ["agent", "autonomous", "tool calling", "multi-agent", "agentic"],
    "Harness": ["harness", "guardrail", "verification", "observability", "evaluation", "state management", "fault tolerance"],
    "Tech Trends": ["model release", "funding", "acquisition", "regulation", "open source", "benchmark"]
  }
}
```
