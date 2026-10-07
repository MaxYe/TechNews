# Scripts 目录说明

所有可执行脚本（从早期 `tmp/` 目录迁入，便于统一管理定时任务脚本）。

## 清单

| 脚本 | 职责 | 被谁调用 |
|------|------|----------|
| `local-prep.py` | Part 0：本地 LLM（qwen3.5）翻译/摘要预处理 | `run-daily.sh` Part 4 |
| `x-collect.sh` | X.com 采集（Mearl 复用浏览器登录态） | `run-daily.sh` Part 3 |
| `x-collect-retry.sh` | X.com 补采（带轮询等待） | 手动 |
| `x-extract.js` | X.com DOM 抽取片段（被 x-collect.sh 内联读取） | `x-collect.sh` |
| `fetch-via-browser.sh` | Cloudflare/登录墙兜底抓取 | `run-daily.sh` Part 2 |
| `push-daily.sh` | 每日 Git 提交+推送 | `run-daily.sh` 完成后手动/云端触发 |

> 注：脚本间通过 `dirname $0` / `__file__` 定位自身与仓库根，可移植。
