# TechNews · 技术新闻每日采集

自动采集 **GitHub / 技术博客 / X.com** 三个渠道的技术动态。用本地小模型完成翻译与摘要，由云端模型补充趋势分析，产出日报与总结并自动推送。

> 最近更新：2026-10-07 ｜ 日报覆盖：2026-10-03 起

## 功能概览

| 渠道 | 采集内容 | 方式 |
|------|----------|------|
| **GitHub Trending** | Daily / Weekly 榜单、语言分榜、生态 Release | curl + HTML 解析 + REST API |
| **外部技术博客** | 10 个源（HN、InfoQ、OpenAI、Anthropic、Google AI 等）| RSS / HTML + 浏览器兜底 |
| **X.com** | 21 个账号（OpenAI / Anthropic / Google DeepMind 官方 + 核心人物 + 中文区）| Mearl 复用浏览器登录态 |

## 目录结构

```
tech-collect/
├── run-daily.sh              # 主编排：采集 + 本地预处理（Part 1-4）
├── config/                   # 三渠道配置
├── scripts/                  # 可执行脚本
│   ├── local-prep.py         #   本地 LLM 翻译/摘要预处理
│   ├── x-collect.sh          #   X.com 采集（Mearl 驱动）
│   ├── x-collect-retry.sh    #   X.com 补采
│   ├── x-extract.js          #   X.com DOM 抽取
│   ├── fetch-via-browser.sh  #   Cloudflare / 登录墙兜底
│   └── push-daily.sh         #   Git 提交 + 推送
├── output/                   # 三渠道日报 github|blog|x/YYYY-MM-DD.md
├── summary/                  # 每日/每周总结 YYYY-MM-DD[-weekly].md
└── tmp/                      # 运行时数据（不入库）raw|x-raw|llm-out
```

## 每日流程

```
08:30 触发
  run-daily.sh            采集 Part1-3 + 本地 LLM 预处理 Part4
    └→ tmp/llm-out/{DATE}/*.json（中文富化）+ output/*/{DATE}-local9b.md（骨架）
  云端模型                 读富化 JSON → 日报×3 + 每日总结（周日加每周总结）
  scripts/push-daily.sh    提交并推送到 GitHub
```

## 快速开始

```bash
cd tech-collect
bash run-daily.sh                    # 完整跑一次（默认今天）
bash run-daily.sh 2026-10-07         # 指定日期

python3 scripts/local-prep.py all    # 只跑本地预处理
bash scripts/x-collect.sh            # 只采 X.com
bash scripts/push-daily.sh           # 只推送

ENABLE_LOCAL_LLM=0 bash run-daily.sh          # 跳过本地预处理
ENABLE_GIT_PUSH=0 bash scripts/push-daily.sh  # 跳过推送
```

## 依赖环境

| 依赖 | 用途 | 缺失时行为 |
|------|------|------------|
| `curl` / `python3` | 采集与解析 | 必须 |
| **ollama** + `qwen3.5-32k` | 本地翻译/摘要 | 自动跳过，云端兜底 |
| **mearl** + Chrome 登录态 | X.com 采集 | 跳过 X.com，其余照常 |
| `git` + SSH key | 推送仓库 | 跳过推送 |

**本地模型**：`qwen3.5-32k` = 由 `qwen3.5:9b` 创建、带 `num_ctx=32768` 的变体。`run-daily.sh` 先检测可用内存（需约 9.2GB），充足才自动启动 ollama，任务结束再关闭释放内存。

## 设计要点

1. **三层分工**：代码负责抽取/分类/编号/统计；本地 9B 负责翻译/摘要/点评；云端负责趋势分析。
2. **模型只出内容，结构由代码组装** —— 避免格式漂移与编号错乱。
3. **防幻觉三原则** —— 显式声明缺失、禁止推测、能算的不用模型。
4. **多级降级** —— curl 失败→浏览器兜底→跳过并标注；本地模型不可用→云端兜底。

详细设计见 [`tech-collect-standalone.md`](doc/tech-collect-standalone.md)。

## 采集统计（截至 2026-10-07）

| 项 | 数量 |
|----|------|
| 日报覆盖 | 5 天（2026-10-03 ~ 10-07）|
| 博客源 | 10 个 |
| X.com 账号 | 21 个 |
| 每日推文 | 40 ~ 88 条 |
