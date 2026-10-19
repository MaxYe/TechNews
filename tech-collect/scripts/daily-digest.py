#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集完成摘要（daily digest）

在 run-daily.sh 收尾时输出**内容级**总结，而不是只列文件路径：
  - 各渠道收录量与质量
  - Top 条目（GitHub 按增量 / X 按互动）
  - 来源分布
  - 关注仓库快照
  - 异常记录

同时写入 tmp/llm-out/{DATE}/digest.md，供后续查阅。

用法：python3 scripts/daily-digest.py [YYYY-MM-DD]
"""
import json
import os
import sys
from collections import Counter
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")


def load(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def fmt(n):
    return f"{n:,}"


def main():
    llm_dir = os.path.join(BASE, "tmp", "llm-out", DATE)
    raw_dir = os.path.join(BASE, "tmp", "raw", DATE)

    gh = load(os.path.join(llm_dir, "github.json")) or []
    blog = load(os.path.join(llm_dir, "blog.json")) or []
    x = load(os.path.join(llm_dir, "x.json")) or []
    detail = load(os.path.join(raw_dir, "repo-detail.json")) or []

    L = []
    L.append("")
    L.append("╔══════════════════════════════════════════════════════════╗")
    L.append(f"║  📊 采集完成摘要 · {DATE}".ljust(61) + "║")
    L.append("╚══════════════════════════════════════════════════════════╝")
    L.append("")

    # ===== GitHub =====
    if gh:
        with_delta = [r for r in gh if r.get("stars", 0) > 0]
        pinned = [r for r in gh if r.get("pinned")]
        official = [r for r in gh if r.get("source") == "trending_html"]
        L.append(f"📦 GitHub：{len(gh)} 个仓库"
                 f"（{len(with_delta)} 含增量 ｜ {len(official)} 官方 ｜ {len(pinned)} 关注）")
        top = sorted(with_delta, key=lambda r: -r.get("stars", 0))[:5]
        if top:
            L.append("   🔥 增量 Top 5：")
            for i, r in enumerate(top, 1):
                L.append(f"      {i}. {r['repo']}  +{fmt(r['stars'])}"
                         f"  (总 {fmt(r.get('total_stars', 0))})")
        else:
            L.append("   ⚠️ 本日无增量数据（首次运行或快照缺失）")
        if pinned:
            L.append("   📌 关注仓库：")
            for r in pinned:
                d = f"+{fmt(r['stars'])}" if r.get("stars") else "—"
                L.append(f"      · {r['repo']}  {d}"
                         f"  总 {fmt(r.get('total_stars', 0))}")
    else:
        L.append("📦 GitHub：❌ 未采集到数据")

    L.append("")

    # ===== 博客 =====
    if blog:
        src_cnt = Counter(b.get("src", "未知") for b in blog)
        L.append(f"📰 博客：{len(blog)} 篇（{len(src_cnt)} 个源）")
        L.append("   来源分布：")
        for s, n in src_cnt.most_common():
            L.append(f"      · {s}: {n} 篇")
        L.append("   最新标题：")
        for b in blog[:5]:
            t = (b.get("title_cn") or b.get("title") or "")[:52]
            L.append(f"      · [{b.get('src', '?')}] {t}")
    else:
        L.append("📰 博客：❌ 未采集到数据")

    L.append("")

    # ===== X.com =====
    if x:
        L.append(f"💬 X.com：{len(x)} 条推文"
                 f"（{len(set(t.get('user') for t in x))} 个账号）")
        top = sorted(x, key=lambda t: -t.get("likes", 0))[:5]
        L.append("   🔥 互动 Top 5：")
        for i, t in enumerate(top, 1):
            txt = (t.get("translation") or "")[:44]
            L.append(f"      {i}. @{t.get('user')}  ❤️{fmt(t.get('likes', 0))}"
                     f"  {txt}")
    else:
        L.append("💬 X.com：❌ 未采集到数据")

    L.append("")

    # ===== 关注仓库深度 =====
    if detail:
        L.append(f"🔍 关注仓库深度采集：{len(detail)} 个")
        for d in detail:
            rel = (d.get("latest_release") or {}).get("tag") or "无"
            L.append(f"      · {d['repo']}  ★{fmt(d['stars'])}"
                     f"  Fork {fmt(d['forks'])}"
                     f"  近7日提交 {d['commits_7d']}"
                     f"  Release {rel}")
        L.append("")

    # ===== 数据质量与异常 =====
    L.append("─" * 58)
    issues = []
    if not gh:
        issues.append("GitHub 无数据")
    elif len(gh) < 20:
        issues.append(f"GitHub 收录偏少（{len(gh)}）")
    if not blog:
        issues.append("博客无数据")
    elif len(blog) < 10:
        issues.append(f"博客收录偏少（{len(blog)}）")
    if not x:
        issues.append("X.com 无数据")
    elif len(x) < 10:
        issues.append(f"X.com 收录偏少（{len(x)}）")

    if issues:
        L.append("⚠️ 数据质量提示：")
        for i in issues:
            L.append(f"   · {i}")
    else:
        L.append("✅ 三渠道数据完整，无明显异常")

    total = len(gh) + len(blog) + len(x)
    L.append("")
    L.append(f"📈 合计收录 {total} 条（GitHub {len(gh)} ｜ 博客 {len(blog)} ｜ X {len(x)}）")
    L.append("")

    text = "\n".join(L)
    print(text)

    # 落盘
    try:
        os.makedirs(llm_dir, exist_ok=True)
        p = os.path.join(llm_dir, "digest.md")
        open(p, "w", encoding="utf-8").write(text + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
