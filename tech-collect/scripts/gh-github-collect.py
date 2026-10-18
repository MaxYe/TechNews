#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub Trending 采集（gh CLI 优先）

为什么用 gh：
  - 已认证，核心 API 5000 次/小时（匿名仅 60）
  - 返回结构化 JSON，不依赖 HTML 结构，不会因页面改版而失效
  - trending 网页不稳定（2026-10-08 截断、10-18 空响应）

为什么还要算增量：
  GitHub Search API 不提供「今日 Star 增量」。真正的 Trending 语义是
  **Star 增长速度**，不是总 Star 数。所以：
    1. 抓取「近期新建 + 高 Star」候选集
    2. 与最近一次历史快照对比，算出 stars_today
    3. 首次运行（无历史）时 stars_today=0，报告需标注为「近似」

输出（兼容 local-prep.py）：
  tmp/raw/{DATE}/github-daily.json
  字段：repo, desc, lang, stars_today, total_stars, created, forks, url, source
  source: gh_search_delta | gh_search_new | gh_search_total
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")

JSON_FIELDS = ("fullName,description,language,stargazersCount,forksCount,"
               "createdAt,pushedAt,url,isArchived,isFork,license")


def run_gh(args, timeout=120):
    """执行 gh 命令，返回 (stdout, stderr, ok)"""
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout, r.stderr.strip(), r.returncode == 0
    except FileNotFoundError:
        return "", "gh CLI 未安装", False
    except subprocess.TimeoutExpired:
        return "", f"gh 命令超时（{timeout}s）", False
    except Exception as e:
        return "", str(e), False


def search(query, limit=30):
    """gh search repos，返回仓库列表"""
    out, err, ok = run_gh([
        "search", "repos", query,
        "--sort=stars", "--order=desc",
        f"--limit={limit}",
        f"--json={JSON_FIELDS}",
    ])
    if not ok:
        print(f"  ⚠️ 查询失败 [{query}]: {err[:120]}")
        return []
    try:
        return json.loads(out)
    except Exception as e:
        print(f"  ⚠️ JSON 解析失败: {e}")
        return []


def load_prev_snapshot():
    """读取最近一次含 total_stars 的历史快照，返回 ({repo: stars}, 日期)"""
    pattern = os.path.join(BASE, "tmp", "raw")
    if not os.path.isdir(pattern):
        return {}, None
    dates = sorted([d for d in os.listdir(pattern)
                    if d.startswith("2") and len(d) == 10], reverse=True)
    for d in dates:
        if d == DATE:
            continue
        p = os.path.join(pattern, d, "github-daily.json")
        if not os.path.isfile(p):
            continue
        try:
            data = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        m = {}
        for r in data:
            name = r.get("repo")
            total = r.get("total_stars")
            if name and isinstance(total, int) and total > 0:
                m[name] = total
        if m:
            return m, d
    return {}, None


def main():
    days_new = 7
    days_growth = 90
    since_new = (datetime.now() - timedelta(days=days_new)).strftime("%Y-%m-%d")
    since_growth = (datetime.now() - timedelta(days=days_growth)).strftime("%Y-%m-%d")

    print(f"[gh] GitHub 采集 {DATE}")
    print(f"  查询1：新上榜 created:>{since_new}")
    new_repos = search(f"created:>{since_new}", limit=30)
    print(f"    → {len(new_repos)} 个")

    print(f"  查询2：近期快速增长 created:>{since_growth}")
    growth_repos = search(f"created:>{since_growth}", limit=50)
    print(f"    → {len(growth_repos)} 个")

    # 合并去重
    merged = {}
    for r in new_repos + growth_repos:
        name = r.get("fullName")
        if not name:
            continue
        if name in merged:
            continue
        merged[name] = r
    print(f"  合并去重：{len(merged)} 个")

    if not merged:
        print("  ❌ 所有查询均无结果")
        return 1

    # 读取历史快照，计算增量
    prev, prev_date = load_prev_snapshot()
    if prev:
        print(f"  历史快照：{prev_date}（{len(prev)} 个仓库）→ 可计算增量")
    else:
        print("  ⚠️ 无历史快照 → stars_today 记为 0（首轮运行属正常）")

    out = []
    for name, r in merged.items():
        total = r.get("stargazersCount") or 0
        prev_total = prev.get(name)
        if prev_total is not None and total >= prev_total:
            delta = total - prev_total
            source = "gh_search_delta"
        else:
            delta = 0
            source = "gh_search_new" if name in {x.get("fullName") for x in new_repos} else "gh_search_total"
        lic = r.get("license") or {}
        out.append({
            "repo": name,
            "desc": (r.get("description") or "")[:200],
            "lang": r.get("language") or "",
            "stars_today": delta,
            "total_stars": total,
            "forks": r.get("forksCount") or 0,
            "created": (r.get("createdAt") or "")[:10],
            "url": r.get("url") or f"https://github.com/{name}",
            "source": source,
        })

    # 排序：有增量的按增量降序，无增量的按总星降序
    out.sort(key=lambda x: (x["stars_today"], x["total_stars"]), reverse=True)
    out = out[:30]

    ddir = os.path.join(BASE, "tmp", "raw", DATE)
    os.makedirs(ddir, exist_ok=True)
    dst = os.path.join(ddir, "github-daily.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    with_delta = sum(1 for x in out if x["stars_today"] > 0)
    print(f"  ✅ 已保存 {len(out)} 个仓库 → {os.path.relpath(dst, BASE)}")
    print(f"     其中 {with_delta} 个含 Star 增量")
    for x in out[:5]:
        d = f"+{x['stars_today']:,}" if x["stars_today"] else f"总{x['total_stars']:,}"
        print(f"     {x['repo']} ({x['lang'] or '-'}) {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
