#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
关注仓库深度采集（watchlist detail）

对 config/github-sources.json 中的 watchlist 仓库，拉取比「元数据」更深的内容：
  - README 正文（截断）
  - 最新 Release
  - 最近提交时间与近 7 天提交数
  - 语言构成
  - Topics / License / Homepage

输出：tmp/raw/{DATE}/repo-detail.json
用途：生成 output/github/{DATE}-pinned.md 深度档案，并供每日总结引用

用法：python3 scripts/gh-repo-detail.py [YYYY-MM-DD]
"""
import base64
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATE = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")


def gh(args, timeout=60):
    """执行 gh 命令，返回 (stdout, ok)"""
    try:
        r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                           timeout=timeout)
        return r.stdout, r.returncode == 0
    except Exception:
        return "", False


def gh_json(args, timeout=60, default=None):
    out, ok = gh(args, timeout)
    if not ok or not out.strip():
        return default
    try:
        return json.loads(out)
    except Exception:
        return default


def load_watchlist():
    p = os.path.join(BASE, "config", "github-sources.json")
    try:
        cfg = json.load(open(p, encoding="utf-8"))
        return cfg.get("watchlist") or []
    except Exception as e:
        print(f"  ⚠️ 配置读取失败: {e}")
        return []


def fetch_detail(full):
    """拉取单个仓库的深度信息"""
    d = gh_json(["api", f"repos/{full}", "--jq",
                 "{repo:.full_name,desc:.description,lang:.language,"
                 "stars:.stargazers_count,forks:.forks_count,"
                 "watchers:.subscribers_count,open_issues:.open_issues_count,"
                 "created:.created_at,pushed:.pushed_at,updated:.updated_at,"
                 "topics:.topics,license:.license.spdx_id,"
                 "homepage:.homepage,default_branch:.default_branch,"
                 "size_kb:.size,archived:.archived}"])
    if not d:
        print(f"    ⚠️ {full}: 基础信息获取失败")
        return None

    # README
    readme = gh_json(["api", f"repos/{full}/readme", "--jq", "{content:.content,size:.size}"])
    readme_text = ""
    if readme and readme.get("content"):
        try:
            raw = base64.b64decode(readme["content"]).decode("utf-8", "ignore")
            readme_text = raw
        except Exception:
            pass
    # 截断：保留前 6000 字符（供 LLM 摘要）
    readme_excerpt = readme_text[:6000]

    # 最新 Release
    rel = gh_json(["api", f"repos/{full}/releases/latest", "--jq",
                   "{tag:.tag_name,name:.name,published:.published_at,url:.html_url}"],
                  default={})

    # 最近提交
    commits = gh_json(["api", f"repos/{full}/commits?per_page=100"], default=[])
    since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = 0
    last_commit = ""
    if isinstance(commits, list):
        for c in commits:
            dt = ((c.get("commit") or {}).get("committer") or {}).get("date") or ""
            if not last_commit and dt:
                last_commit = dt
            if dt and dt >= since:
                recent += 1

    # 语言构成
    langs = gh_json(["api", f"repos/{full}/languages"], default={})
    total_bytes = sum(langs.values()) if isinstance(langs, dict) else 0
    lang_pct = []
    if total_bytes:
        for k, v in sorted(langs.items(), key=lambda x: -x[1])[:5]:
            lang_pct.append({"lang": k, "pct": round(v * 100.0 / total_bytes, 1)})

    return {
        "repo": d.get("repo") or full,
        "desc": d.get("desc") or "",
        "lang": d.get("lang") or "",
        "stars": d.get("stars") or 0,
        "forks": d.get("forks") or 0,
        "watchers": d.get("watchers") or 0,
        "open_issues": d.get("open_issues") or 0,
        "created": (d.get("created") or "")[:10],
        "pushed": (d.get("pushed") or "")[:19],
        "topics": d.get("topics") or [],
        "license": d.get("license") or "",
        "homepage": d.get("homepage") or "",
        "size_kb": d.get("size_kb") or 0,
        "archived": d.get("archived") or False,
        "readme_chars": len(readme_text),
        "readme_excerpt": readme_excerpt,
        "latest_release": rel or {},
        "commits_7d": recent,
        "last_commit": last_commit[:19],
        "lang_pct": lang_pct,
    }


def main():
    watch = load_watchlist()
    if not watch:
        print("  ⚠️ watchlist 为空，跳过深度采集")
        return 0

    print(f"[detail] 关注仓库深度采集 {DATE}（{len(watch)} 个）")
    out = []
    for full in watch:
        print(f"  → {full}")
        d = fetch_detail(full)
        if d:
            out.append(d)
            rel = d["latest_release"].get("tag") or "无"
            print(f"      stars={d['stars']:,} forks={d['forks']:,} "
                  f"README={d['readme_chars']}字 release={rel} "
                  f"近7日提交={d['commits_7d']}")

    if not out:
        print("  ❌ 全部失败")
        return 1

    ddir = os.path.join(BASE, "tmp", "raw", DATE)
    os.makedirs(ddir, exist_ok=True)
    dst = os.path.join(ddir, "repo-detail.json")
    json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"  ✅ 已保存 {len(out)} 个仓库详情 → {os.path.relpath(dst, BASE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
