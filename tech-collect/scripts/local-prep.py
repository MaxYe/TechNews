#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日采集的「本地 LLM 预处理」步骤 —— 把翻译/摘要交给本地 qwen3.5，为云端模型减负。

设计（遵循 /local-llm-translate-summarize skill）：
  本地 9B  负责：翻译 + 逐条摘要（确定性文本转换）
  代码     负责：抽取、分类打标、编号、链接、统计（能算的不用模型）
  云端模型 负责：跨源趋势分析（9B 幻觉率高，交回云端）

数据流：
  原始数据 → 抽取 → 分类(代码) → items.json
          → skill helper 批量翻译/摘要 → 结果 JSON
          → ① 组装报告骨架 (-local9b.md)
          → ② 产出「中文富化 JSON」供云端模型读（替代读原始英文数据）

用法:
  python3 local-prep.py github [--date YYYY-MM-DD]
  python3 local-prep.py blog   [--date YYYY-MM-DD]
  python3 local-prep.py x       [--date YYYY-MM-DD]
  python3 local-prep.py all    [--date YYYY-MM-DD]
  python3 local-prep.py ping
"""
import json
import os
import re
import sys
import glob
import subprocess
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_DIR = os.path.join(os.environ.get("HOME", ""), ".dsh", "skills", "local-llm-translate-summarize")
SKILL_HELPER = os.path.join(SKILL_DIR, "scripts", "local_llm.py")
LLM_OUT = os.path.join(BASE, "tmp", "llm-out")
WINDOW_DAYS = int(os.environ.get("TIME_WINDOW", "3"))
BATCH = int(os.environ.get("BATCH_SIZE", "5"))


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ============================================================
# 基础工具
# ============================================================
def load_config(name):
    p = os.path.join(BASE, "config", name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}


def clean(s):
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s or "")
    # 先反转义 HTML 实体，再剥标签（顺序关键：否则 &lt;img&gt; 反转义成 <img> 后已无法被剥除）
    for a, b in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#8217;", "'"), ("&#8212;", "—"), ("&#39;", "'"), ("&nbsp;", " "),
                 ("&apos;", "'")]:
        s = s.replace(a, b)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def classify(text, keyword_map):
    """分类打标用代码做（skill 明确：模型会输出空标签）"""
    t = (text or "").lower()
    tags = []
    for tag, kws in keyword_map.items():
        for kw in kws:
            if kw.lower() in t:
                tags.append(tag)
                break
    return tags[:2]


def tags_fmt(tags):
    return " ".join(f"`{t}`" for t in tags) if tags else "`未分类`"


def parse_rss_date(s):
    if not s:
        return None
    s = s.strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:25] if "," in s else s[:19], fmt)
        except Exception:
            continue
    return None


def call_skill(cmd, items):
    """调用 skill helper：batch-summary / batch-translate。返回合并后的结果列表。"""
    if not os.path.exists(SKILL_HELPER):
        log(f"⚠️ skill helper 不存在: {SKILL_HELPER}")
        return None
    items_path = os.path.join(LLM_OUT, "_items.json")
    os.makedirs(LLM_OUT, exist_ok=True)
    json.dump(items, open(items_path, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        p = subprocess.run(
            [sys.executable, SKILL_HELPER, cmd, "--json", items_path],
            capture_output=True, text=True, timeout=1800,
        )
        if p.returncode != 0:
            log(f"⚠️ skill helper 调用失败: {p.stderr[:120]}")
            return None
        out = p.stdout.strip()
        return json.loads(out) if out else None
    except Exception as e:
        log(f"⚠️ skill helper 异常: {str(e)[:120]}")
        return None


def call_skill_with_fill(cmd, items, required_keys, batch=None):
    """
    调用 skill helper 并补齐漏条（9B 批量 JSON 会随机漏条目）。
    逐条检查 required_keys 是否都有值；缺失的用更小批次重试。
    返回按 i 索引的结果 dict。
    """
    results = {}
    pending = list(enumerate(items))
    retry = 0
    while pending and retry < 3:
        if retry == 0:
            # 首次：整批调用
            resp = call_skill(cmd, [x for _, x in pending])
        else:
            # 重试：用更小批次（逐个或 2 个）
            chunk = max(1, (batch or BATCH) // (retry + 1))
            resp = []
            for i in range(0, len(pending), chunk):
                sub = pending[i:i + chunk]
                r = call_skill(cmd, [{"i": j, **x} for j, (_, x) in enumerate(sub)])
                # 重新映射回原始 i
                if r:
                    for obj in r:
                        obj = dict(obj)
                        # 重试时 helper 返回的 i 是子批次内的，需要映射
                        orig_i = sub[obj.get("i", 0)][0]
                        obj["_orig_i"] = orig_i
                        resp.append(obj)
                else:
                    resp.append(None)
        # 合并结果
        still_pending = []
        if resp is None:
            # 整批失败，全部待重试
            still_pending = pending
        else:
            for pos, (orig_i, item) in enumerate(pending):
                got = None
                if isinstance(resp, list):
                    if retry == 0:
                        # 首次：按 i 找
                        for obj in resp or []:
                            if int(obj.get("i", -1)) == orig_i:
                                got = obj
                                break
                    else:
                        # 重试：按 _orig_i 找
                        for obj in resp or []:
                            if obj.get("_orig_i") == orig_i:
                                got = obj
                                break
                if got and all(str(got.get(k, "")).strip() for k in required_keys):
                    results[orig_i] = got
                else:
                    still_pending.append((orig_i, item))
        pending = still_pending
        retry += 1
        if still_pending:
            log(f"    ⚠️ 漏条 {len(still_pending)} 个，第 {retry} 次重试（小批次）")
    if pending:
        log(f"    ⚠️ 仍有 {len(pending)} 个条目模型未返回，将用占位符")
    return results


# ============================================================
# 抽取（项目特定）
# ============================================================
def extract_github(date):
    src = os.path.join(BASE, "tmp", "raw", date, "github-daily.json")
    if not os.path.exists(src):
        return []
    kw = load_config("github-sources.json").get("topic_keywords", {})
    items = []
    for r in json.load(open(src, encoding="utf-8")):
        desc = r.get("desc", "") or ""
        items.append({
            "repo": r["repo"], "lang": r.get("lang", ""),
            "stars": r.get("stars_today", 0), "desc": desc,
            "text": desc or "（数据源无描述）",          # skill helper 读 text
            "tags": classify(f"{r['repo']} {desc}", kw),
        })
    return items


def extract_blog(date):
    raw = os.path.join(BASE, "tmp", "raw", date)
    cutoff = datetime.strptime(date, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS)
    kw = load_config("blog-sources.json").get("topic_keywords", {})
    items = []
    for f in sorted(glob.glob(os.path.join(raw, "*.xml"))):
        base = os.path.basename(f)
        if base.startswith("github-"):
            continue
        src = base.replace("blog-", "").replace(".xml", "")
        t = open(f, encoding="utf-8", errors="ignore").read()
        for b in re.split(r"<item>", t)[1:12]:
            tm = re.search(r"<title[^>]*>(.*?)</title>", b, re.DOTALL)
            dm = re.search(r"<(pubDate|published|updated)>(.*?)</\1>", b, re.DOTALL)
            cm = re.search(r"<(?:description|summary|content:encoded)>(.*?)</(?:description|summary|content:encoded)>",
                           b, re.DOTALL)
            lm = re.search(r'<link[^>]*href="([^"]+)"', b) or re.search(r"<link>(.*?)</link>", b, re.DOTALL)
            if not tm:
                continue
            dt = parse_rss_date(dm.group(2) if dm else "")
            if dt and dt < cutoff:
                continue
            title = clean(tm.group(1))
            desc = clean(cm.group(1) if cm else "")[:350]
            items.append({
                "src": src, "title": title, "desc": desc,
                "text": desc or "（无摘要）",
                "link": (lm.group(1).strip() if lm else ""),
                "date": dt.strftime("%Y-%m-%d") if dt else "",
                "tags": classify(f"{title} {desc}", kw),
            })
    # Anthropic HTML
    hp = os.path.join(raw, "anthropic-news.html")
    if os.path.exists(hp):
        ht = open(hp, encoding="utf-8", errors="ignore").read()
        pat = re.compile(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),\s+(2026)"
                         r"(.{0,500}?)href=\"(/news/[a-z0-9\-]+)\"", re.DOTALL)
        months = {m: i for i, m in enumerate(
            ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
        seen = set()
        for m in pat.finditer(ht):
            mon, day, yr, mid, slug = m.groups()
            try:
                dt = datetime(int(yr), months[mon], int(day))
            except Exception:
                continue
            if dt < cutoff or slug in seen:
                continue
            parts = [p.strip() for p in re.sub(r"<[^>]+>", "|", mid).split("|") if len(p.strip()) > 15]
            title = clean(parts[-1]) if parts else slug.rsplit("/", 1)[-1].replace("-", " ").title()
            seen.add(slug)
            items.append({"src": "anthropic-news", "title": title, "desc": "",
                          "text": "（无摘要，仅标题）", "link": "https://www.anthropic.com" + slug,
                          "date": dt.strftime("%Y-%m-%d"), "tags": classify(title, kw)})
    # 去重
    uniq, seen = [], set()
    for it in items:
        k = it["title"][:40]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


def extract_x(date):
    cutoff = (datetime.strptime(date, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    cfg = load_config("x-sources.json")
    kw = cfg.get("topic_keywords", {})
    meta = {}
    for tier in ("tier1_official", "tier2_key_people", "tier3_bonus"):
        for a in cfg.get("sources", {}).get(tier, []):
            meta[a["username"]] = a
    items = []
    for f in glob.glob(os.path.join(BASE, "tmp", "x-raw", "*.json")):
        d = json.load(open(f, encoding="utf-8"))
        u = d["username"]
        for t in d.get("tweets", []):
            if t.get("date", "")[:10] < cutoff:
                continue
            text = t.get("text", "").replace("\n", " ")[:400]
            items.append({
                "user": u, "name": meta.get(u, {}).get("display_name", u),
                "category": meta.get(u, {}).get("category", "其他"),
                "role": meta.get(u, {}).get("role", ""),
                "date": t["date"][:10], "text": text,
                "likes": t.get("likes", 0), "rt": t.get("retweets", 0),
                "replies": t.get("replies", 0), "views": t.get("views", 0),
                "url": t.get("url", ""), "tags": classify(text, kw),
            })
    items.sort(key=lambda x: -x["likes"])
    return items


# ============================================================
# 组装
# ============================================================
def assemble(items, results_dict):
    """把 skill helper 的结果（dict: {原始i: obj}）合并回 items，返回富化列表。"""
    for i, it in enumerate(items):
        it["_i"] = i
        r = (results_dict or {}).get(i, {})
        it.update(r)
    return items


def write_json(items, date, kind):
    os.makedirs(os.path.join(LLM_OUT, date), exist_ok=True)
    p = os.path.join(LLM_OUT, date, f"{kind}.json")
    json.dump(items, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"  ✓ 中文富化 JSON（供云端模型读）: {p}")


def write_md_github(items, date):
    out = [f"""# GitHub Trending - {date}（本地 qwen3.5 预处理）

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 数据来源：tmp/raw/{date}/github-daily.json（{len(items)} 个仓库）
> 说明：描述翻译与亮点由本地 9B 生成；分类标签、编号、链接、统计由代码生成
> ⚠️ 趋势分析待云端模型补充

## 当日 Trending

"""]
    for it in items:
        desc = it.get("desc") or ""
        trans = (it.get("translation") or "").strip() or "（翻译缺失）"
        hl = (it.get("highlight") or "").strip() or "（亮点缺失）"
        if desc:
            desc_line = f"{desc}（{trans}）"
        else:
            desc_line = trans if trans.startswith("（") else f"（无描述）{trans}"
        out.append(f"""### {it['_i']+1}. {it['repo']}
- 描述：{desc_line}
- 语言：{it['lang'] or '未知'}
- 今日 Star：+{it['stars']:,}
- 今日亮点：{hl}
- 分类标签：{tags_fmt(it['tags'])}
- 链接：https://github.com/{it['repo']}
""")
    p = os.path.join(BASE, "output", "github", f"{date}-local9b.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("\n".join(out))
    log(f"  ✓ 报告骨架: {p}")


def write_md_blog(items, date):
    out = [f"""# 外部技术博客 - {date}（本地 qwen3.5 预处理）

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 数据来源：tmp/raw/{date}/blog-*.xml + anthropic-news.html（{len(items)} 篇）
> 说明：标题中文化与摘要由本地 9B 生成；分类、编号、链接由代码生成
> ⚠️ 跨源趋势分析待云端模型补充

"""]
    by_src = {}
    for it in items:
        by_src.setdefault(it["src"], []).append(it)
    for src, entries in by_src.items():
        out.append(f"## {src}\n")
        for n, it in enumerate(entries, 1):
            out.append(f"""### {n}. {it.get('title_cn') or it['title']}
- 来源：{src}
- 日期：{it.get('date') or '未知'}
- 原文标题：{it['title']}
- 链接：{it.get('link') or '（无链接）'}
- 摘要：{(it.get('summary') or '').strip() or '（摘要缺失）'}
- 分类：{tags_fmt(it['tags'])}
""")
    p = os.path.join(BASE, "output", "blog", f"{date}-local9b.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("\n".join(out))
    log(f"  ✓ 报告骨架: {p}")


def write_md_x(items, date):
    out = [f"""# X.com 技术观察 - {date}（本地 qwen3.5 预处理）

> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}
> 数据来源：tmp/x-raw/*.json（窗口内 {len(items)} 条）
> 说明：翻译与点评由本地 9B 生成；互动数据为代码直取（非模型生成）
> ⚠️ 跨账号信号观察待云端模型补充

"""]
    by_cat = {}
    for it in items:
        by_cat.setdefault(it["category"], []).append(it)
    for cat, entries in by_cat.items():
        out.append(f"## {cat}\n")
        for n, it in enumerate(entries, 1):
            role = f"（{it['role']}）" if it.get("role") else ""
            out.append(f"""### {n}. @{it['user']} — {it['name']}{role}
- 日期：{it['date']}
- 链接：{it.get('url') or '（无链接）'}
- 互动：❤️{it['likes']:,} 🔄{it['rt']:,} 💬{it['replies']:,} 👁{it['views']:,}
- 内容：{(it.get('translation') or '').strip() or '（翻译缺失）'}
- 标签：{tags_fmt(it['tags'])}
- 点评：{(it.get('highlight') or '').strip() or '（点评缺失）'}
""")
    p = os.path.join(BASE, "output", "x", f"{date}-local9b.md")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w", encoding="utf-8").write("\n".join(out))
    log(f"  ✓ 报告骨架: {p}")


# ============================================================
# 各任务
# ============================================================
def prep_github(date):
    log("📦 GitHub：本地翻译描述")
    items = extract_github(date)
    if not items:
        log("  ⚠️ 无数据，跳过")
        return
    log(f"  抽取 {len(items)} 个仓库")
    # 只送 text 给 skill helper
    llm_items = [{"i": i, "text": it["text"]} for i, it in enumerate(items)]
    results = call_skill_with_fill("batch-translate", llm_items, ["translation", "highlight"])
    items = assemble(items, results)
    write_json(items, date, "github")
    write_md_github(items, date)


def prep_blog(date):
    log("📦 博客：本地摘要")
    items = extract_blog(date)
    if not items:
        log("  ⚠️ 无数据，跳过")
        return
    log(f"  抽取 {len(items)} 篇")
    llm_items = [{"i": i, "src": it["src"], "title": it["title"], "text": it["text"]}
                 for i, it in enumerate(items)]
    results = call_skill_with_fill("batch-summary", llm_items, ["title_cn", "summary"])
    items = assemble(items, results)
    write_json(items, date, "blog")
    write_md_blog(items, date)


def prep_x(date):
    log("📦 X.com：本地翻译+点评")
    items = extract_x(date)
    if not items:
        log("  ⚠️ 无数据，跳过")
        return
    log(f"  抽取 {len(items)} 条推文")
    llm_items = [{"i": i, "text": it["text"]} for i, it in enumerate(items)]
    results = call_skill_with_fill("batch-translate", llm_items, ["translation", "highlight"])
    items = assemble(items, results)
    write_json(items, date, "x")
    write_md_x(items, date)


# ============================================================
# 入口
# ============================================================
def main():
    args = sys.argv[1:]
    task = args[0] if args else "ping"
    date = datetime.now().strftime("%Y-%m-%d")
    if "--date" in args:
        date = args[args.index("--date") + 1]

    # 前置检查：skill helper + ollama + 模型
    if not os.path.exists(SKILL_HELPER):
        log(f"❌ skill helper 缺失: {SKILL_HELPER}")
        sys.exit(1)
    if task == "ping":
        r = subprocess.run([sys.executable, SKILL_HELPER, "ping"], capture_output=True, text=True, timeout=300)
        print(r.stdout, r.stderr)
        return

    t0 = datetime.now()
    if task == "github":
        prep_github(date)
    elif task == "blog":
        prep_blog(date)
    elif task == "x":
        prep_x(date)
    elif task == "all":
        for fn in (prep_github, prep_blog, prep_x):
            fn(date)
    else:
        print(f"未知任务: {task}\n可用: ping | github | blog | x | all")
        sys.exit(1)
    log(f"总耗时 {(datetime.now()-t0).total_seconds()/60:.1f} 分钟")


if __name__ == "__main__":
    main()