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


def load_config():
    """读取 config/github-sources.json（watchlist / hot_topics 等）"""
    p = os.path.join(BASE, "config", "github-sources.json")
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️ 配置读取失败: {e}")
        return {}


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


def fetch_trending_html():
    """
    尽力抓取 trending 网页，解析真实的「今日 Star 增量」。
    这是唯一能拿到官方增量的来源，但网页不稳定，所以只作为**增强**，失败不影响主流程。
    返回 {repo: stars_today}
    """
    import re
    import urllib.request
    url = "https://github.com/trending?since=daily"
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36")
    })
    html = ""
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", "ignore")
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"    ⚠️ trending 网页不可用（不影响主流程）: {str(e)[:60]}")
        return {}

    if 'class="Box-row"' not in html:
        print("    ⚠️ trending 网页内容异常（无 Box-row）")
        return {}

    out = {}
    for part in html.split('class="Box-row"')[1:]:
        real = re.search(r'href="/([a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)"[^>]*class="Link"', part)
        star = re.search(r'(\d[\d,]*)\s+stars? today', part)
        if real and star:
            out[real.group(1)] = int(star.group(1).replace(",", ""))
    print(f"    → trending 网页解析到 {len(out)} 个仓库的官方增量")
    return out


def main():
    days_new = 7
    days_growth = 90
    days_active = 2
    since_new = (datetime.now() - timedelta(days=days_new)).strftime("%Y-%m-%d")
    since_growth = (datetime.now() - timedelta(days=days_growth)).strftime("%Y-%m-%d")
    since_active = (datetime.now() - timedelta(days=days_active)).strftime("%Y-%m-%d")

    print(f"[gh] GitHub 采集 {DATE}")

    print(f"  查询1：新上榜 created:>{since_new}")
    new_repos = search(f"created:>{since_new}", limit=30)
    print(f"    → {len(new_repos)} 个")

    print(f"  查询2：近期快速增长 created:>{since_growth}")
    growth_repos = search(f"created:>{since_growth}", limit=50)
    print(f"    → {len(growth_repos)} 个")

    # 查询3：老牌活跃热门 —— 覆盖创建较早但突然走红的项目（如 morluto/rea 2026-04 创建却有 32 万 Star）
    print(f"  查询3：老牌活跃热门 pushed:>{since_active} stars:>5000")
    active_repos = search(f"pushed:>{since_active} stars:>5000", limit=60)
    print(f"    → {len(active_repos)} 个")

    # 查询4：配置的热点主题（config/github-sources.json → hot_topics）
    cfg = load_config()
    hot_topics = cfg.get("hot_topics") or []
    topic_repos = []
    if hot_topics:
        print(f"  查询4：热点主题 {len(hot_topics)} 个")
        for t in hot_topics:
            rs = search(f"topic:{t}", limit=20)
            topic_repos.extend(rs)
        print(f"    → 累计 {len(topic_repos)} 个（未去重）")

    # 查询5：关注仓库（config/github-sources.json → watchlist）—— 强制纳入
    watchlist = cfg.get("watchlist") or []
    watched = []
    if watchlist:
        print(f"  查询5：关注仓库 {len(watchlist)} 个")
        for full in watchlist:
            out_s, err_s, ok_s = run_gh([
                "api", f"repos/{full}", "--jq",
                "{fullName:.full_name,description:.description,language:.language,"
                "stargazersCount:.stargazers_count,forksCount:.forks_count,"
                "createdAt:.created_at,url:.html_url,license:.license}"
            ], timeout=30)
            if ok_s and out_s.strip():
                try:
                    watched.append(json.loads(out_s))
                except Exception:
                    pass
            else:
                print(f"    ⚠️ {full}: {err_s[:60] or '查询失败'}")
        print(f"    → {len(watched)} 个")

    watched_names = {x.get("fullName") for x in watched if x.get("fullName")}

    # 增强：trending 网页的官方增量
    print("  增强：抓取 trending 网页官方增量")
    official = fetch_trending_html()

    # 合并去重（记录来源）
    merged = {}
    origin = {}
    for tag, lst in (("new", new_repos), ("growth", growth_repos), ("active", active_repos),
                     ("topic", topic_repos), ("watch", watched)):
        for r in lst:
            name = r.get("fullName")
            if not name or name in merged:
                continue
            merged[name] = r
            origin[name] = tag
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
        # 优先级：官方 trending 增量 > 快照比对增量 > 0
        if name in official:
            delta = official[name]
            source = "trending_html"
        elif prev_total is not None and total >= prev_total:
            delta = total - prev_total
            source = "gh_search_delta"
        else:
            delta = 0
            source = "gh_search_" + origin.get(name, "total")
        lic = r.get("license") or {}
        # 平均日增（总 Star / 创建天数）—— 用于首次纳入、尚无历史快照时的排序兜底
        created_str = (r.get("createdAt") or "")[:10]
        try:
            age_days = max((datetime.now()
                            - datetime.strptime(created_str, "%Y-%m-%d")).days, 1)
        except Exception:
            age_days = 1
        avg_daily = round(total / age_days)
        out.append({
            "repo": name,
            "desc": (r.get("description") or "")[:200],
            "lang": r.get("language") or "",
            "stars_today": delta,
            "avg_daily": avg_daily,
            "total_stars": total,
            "forks": r.get("forksCount") or 0,
            "created": created_str,
            "url": r.get("url") or f"https://github.com/{name}",
            "source": source,
            "pinned": name in watched_names,
        })

    # 排序：今日增量优先；增量缺失时用「平均日增」兜底
    out.sort(key=lambda x: (x["stars_today"], x["avg_daily"], x["total_stars"]),
             reverse=True)

    # Top N：watchlist 强制纳入（置顶），其余按分数补足
    LIMIT = 40
    pinned = [x for x in out if x.get("pinned")]
    rest = [x for x in out if not x.get("pinned")]
    filled = pinned + rest[:max(LIMIT - len(pinned), 0)]
    if pinned:
        print(f"     关注仓库置顶 {len(pinned)} 个")
    out = filled[:LIMIT]

    ddir = os.path.join(BASE, "tmp", "raw", DATE)
    os.makedirs(ddir, exist_ok=True)
    dst = os.path.join(ddir, "github-daily.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    with_delta = sum(1 for x in out if x["stars_today"] > 0)
    n_official = sum(1 for x in out if x["source"] == "trending_html")
    print(f"  ✅ 已保存 {len(out)} 个仓库 → {os.path.relpath(dst, BASE)}")
    print(f"     其中 {with_delta} 个含 Star 增量（{n_official} 个来自官方 trending）")
    for x in out[:5]:
        d = f"+{x['stars_today']:,}" if x["stars_today"] else f"总{x['total_stars']:,}"
        print(f"     {x['repo']} ({x['lang'] or '-'}) {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
