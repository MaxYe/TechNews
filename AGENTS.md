# AGENTS.md

This file provides guidance to Qoder (qoder.com) when working with code in this repository.

面向 AI 编码代理的项目说明。**开始修改前请先读完本文件。**

---

## 1. 项目概述

`TechNews` 是一个**每日技术新闻自动采集系统**：定时抓取 GitHub / 技术博客 / X.com 三个渠道，用本地小模型做翻译摘要、云端模型补趋势分析，产出日报与总结并自动推送到本仓库。

- 详细采集规范：`doc/tech-collect-standalone.md`
- 主编排脚本：`tech-collect/run-daily.sh`
- 运行时数据目录 `tech-collect/tmp/` **不入库**（见 `.gitignore`）

---

## 2. 目录约定与数据流

```
tech-collect/
├── run-daily.sh      # 主编排（采集 Part1-3 + 预处理 Part4）
├── config/           # 源配置 JSON，改采集范围改这里
├── scripts/          # 所有可执行脚本（不要把脚本放别处）
├── output/           # 三渠道日报（入库）
├── summary/          # 每日/每周总结（入库）
└── tmp/              # 运行时数据（不入库，可随时清理）
```

**数据流路径（Part 1-4 → 云端 → 推送）**：

```
Part 1: GitHub      → tmp/raw/{DATE}/github-daily.json
Part 2: 博客         → tmp/raw/{DATE}/blog-*.xml + anthropic-news.html
Part 3: X.com       → tmp/x-raw/{username}.json
Part 4: 本地 LLM     → tmp/llm-out/{DATE}/{github,blog,x}.json
                     → output/{channel}/{DATE}-local9b.md（骨架）
云端模型读富化 JSON  → output/{channel}/{DATE}.md + summary/{DATE}.md
push-daily.sh       → git commit + push
```

**硬性约定**：
- 新增脚本一律放 `tech-collect/scripts/`，**不要**放 `tmp/`
- `tmp/` 只放运行时数据：`raw/{DATE}/`、`x-raw/`、`llm-out/{DATE}/`
- 输出文件命名：`output/{channel}/YYYY-MM-DD.md`、`summary/YYYY-MM-DD.md`、`summary/YYYY-MM-DD-weekly.md`
- 脚本内**不要写死绝对路径**，用 `$(cd "$(dirname "$0")/.." && pwd)` 或 Python 的 `__file__` 定位仓库根

---

## 3. 三层分工（改代码前必须理解）

| 层 | 负责 | 禁止 |
|----|------|------|
| **代码**（shell/python） | 数据抽取、分类打标、编号、链接、统计 | —— |
| **本地 9B**（ollama `qwen3.5-32k`） | 翻译、逐条摘要、一句话点评 | 不要让它做数字统计、分类打标、跨源趋势 |
| **云端模型** | 跨源趋势分析、跨账号信号观察 | —— |

**核心设计：模型只出内容，结构由代码组装。**
让模型直接输出完整 Markdown 会导致编号错乱、格式漂移。正确做法是让模型输出 JSON（如 `[{"i":0,"summary":"..."}]`），再由代码拼装结构。

**配置驱动分类打标**：`config/*-sources.json` 中的 `topic_keywords` 字段定义关键词到标签的映射，`local-prep.py` 的 `classify()` 函数用纯关键词匹配（不经模型）为条目打标签。

---

## 4. 本地 LLM 调用契约（配错就跑不起来）

调用 ollama 时**必须**：

```json
{
  "think": false,
  "options": { "num_ctx": 32768, "temperature": 0.3, "top_p": 0.9 }
}
```

| 参数 | 必须值 | 原因 |
|------|--------|------|
| `think` | `false` | ⚠️ 默认 `true` 会生成超长思维链，实测 **>7 分钟无响应**（像卡死）|
| `num_ctx` | `32768` | 模型原生 256K，但 ollama **默认只给 4096**，稍长文本即被截断 |
| `temperature` | `0.3` | 默认 1.0 输出发散、术语不一致 |

**内存约束**：`qwen3.5-32k` 加载需约 **9.2GB**。`run-daily.sh` 会先检测可用内存（`OLLAMA_MEM_NEED_GB` 环境变量可调，默认 10.0），充足才启动 ollama，任务结束后关闭释放。修改 Part 4 时不要破坏这个逻辑。

**Skill Helper 依赖**：`local-prep.py` 通过外部 skill helper（路径 `~/.dsh/skills/local-llm-translate-summarize/scripts/local_llm.py`）执行批量翻译/摘要。Helper 提供 `batch-translate` 和 `batch-summary` 两种命令。

**漏条补齐机制**：`call_skill_with_fill()` 对模型随机漏返的条目做最多 3 轮小批次重试（批次逐步缩小到 1），仍缺失则用占位符标记。

---

## 5. 防幻觉三原则（改提示词时必须保留）

实测 9B 在数据缺失时会**编造内容**（例：仓库无描述，模型自行编了用途）。必须：

| 原则 | 做法 |
|------|------|
| ① 显式声明缺失 | 输入标注「（数据源无描述）」+ 指令「必须输出『（无描述）』」 |
| ② 禁止推测 | 明确写「不得推测」「严禁编造」 |
| ③ 代码侧兜底 | 分类标签、数字、链接、互动数据**全部由代码生成**，不交给模型 |

---

## 6. 多级降级（不要写成硬失败）

采集与处理必须逐级降级，任一环节失败不能中断整条流水线：

| 环节 | 降级链 |
|------|--------|
| 博客抓取 | curl 重试 → `scripts/fetch-via-browser.sh` 浏览器兜底 → 跳过并标注 ❌ |
| X.com 采集 | mearl 未连接 → 跳过 X.com，其余照常 |
| 本地 LLM | ollama 不可用 / 内存不足 → 跳过 Part 4，云端兜底 |
| 推送 | 失败只记日志，不影响已产出的报告 |

**报告里必须如实记录异常**，不允许静默跳过。

---

## 7. 常用命令与环境变量

```bash
cd tech-collect

# 完整跑一次
bash run-daily.sh [YYYY-MM-DD]

# 单环节
python3 scripts/local-prep.py all --date YYYY-MM-DD   # 本地 LLM 预处理
python3 scripts/local-prep.py github --date YYYY-MM-DD # 只处理 GitHub
python3 scripts/local-prep.py blog --date YYYY-MM-DD   # 只处理博客
python3 scripts/local-prep.py x --date YYYY-MM-DD      # 只处理 X.com
python3 scripts/local-prep.py ping                    # 连通性检查
bash scripts/x-collect.sh [账号...]                   # X.com 采集
bash scripts/x-collect-retry.sh [账号...]             # X.com 补采（带轮询等待）
bash scripts/push-daily.sh [YYYY-MM-DD]               # 提交并推送

# 环境变量开关
ENABLE_LOCAL_LLM=0 bash run-daily.sh          # 跳过本地预处理
ENABLE_GIT_PUSH=0 bash scripts/push-daily.sh   # 跳过推送
TIME_WINDOW=7 python3 scripts/local-prep.py all  # 采集时间窗口（天），默认 3
BATCH_SIZE=4 python3 scripts/local-prep.py all   # LLM 每批条数，默认 5
OLLAMA_MEM_NEED_GB=12 bash run-daily.sh          # 内存阈值，默认 10.0
```

改完脚本**务必**跑 `bash -n <script>`（shell）或 `python3 -c "import ast;ast.parse(open(f).read())"`（python）验证语法。

---

## 8. Git 推送约定

推送统一走 `scripts/push-daily.sh`，它在**单次执行内**完成：

```
init → remote add → fetch → checkout -B main origin/main → add -A → commit → push
```

**这个顺序不能改**：
- 必须先 `fetch` + `checkout -B main origin/main`，基于远端历史提交，否则会因非快进被拒
- 必须在一个脚本调用内完成，不要拆成多步手工操作

---

## 9. 已知环境问题

**文件系统写入回滚**：在某些会话中，工作区的新写入会在命令结束后被自动回滚（脚本报成功但文件未落盘）。表现为：

- `run-daily.sh`、`scripts/*` 等**已存在的文件**修改可能正常
- **新建文件**（尤其 `README.md`、`.git/objects`、`.git/refs`）可能被回滚
- `.git` 元数据回滚会导致「本地提交消失、远端无更新」

**应对**：把「写入 + commit + push」放在**同一个命令调用内**完成（`push-daily.sh` 就是这么设计的）。若持续失败，重启会话后重试。

---

## 10. 提交前自检清单

- [ ] 脚本语法通过（`bash -n` / `ast.parse`）
- [ ] 没有硬编码绝对路径
- [ ] 新脚本放在 `scripts/`，不是 `tmp/`
- [ ] 提示词保留了防幻觉三原则
- [ ] 降级链完整，失败有日志与报告记录
- [ ] 本地 LLM 调用带 `think:false` + `num_ctx:32768`
- [ ] `tmp/` 下的新数据路径已加入 `.gitignore`
- [ ] 提交使用 noreply 邮箱（避免 GH007）

---

## 11. GitHub 推送邮箱（GH007）

GitHub 会拒绝推送作者邮箱为私人邮箱的提交：

    remote: error: GH007: Your push would publish a private email address.

**提交时必须显式指定 noreply 邮箱**，不要依赖全局 git config：

    git -c user.name=MaxYe -c user.email=MaxYe@users.noreply.github.com commit -m "..."

改写已存在的提交（未推送前安全）：

    git -c user.name=MaxYe -c user.email=MaxYe@users.noreply.github.com commit --amend --no-edit --author="MaxYe <MaxYe@users.noreply.github.com>"

或在 GitHub 设置里关闭拦截：https://github.com/settings/emails

`scripts/push-daily.sh` 已内置 noreply 邮箱，走它推送不会触发该问题。
