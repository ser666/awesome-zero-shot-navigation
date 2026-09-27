"""链接发现 —— 为论文找出「PDF / 项目主页 / 代码仓库」链接

为什么要单独做
--------------
Boss 要求：**除了 PDF，如果有 project 网页或代码仓库，也加链接在后面**。
但 OpenAlex / Crossref 都不提供代码仓库字段，所以需要多路发现：

  ① 摘要 + arXiv comment 里的 URL          ← 精度最高（作者自己贴的）
  ② GitHub Search API 按标题找仓库          ← 覆盖面广，但要防误配
  ③ Unpaywall 查开放获取 PDF                ← 补"没有 arXiv 版"的论文
  ④ DOI / arXiv 主页兜底                    ← 保证每条至少有一个可点链接

⚠️ 关键风险：**误配**。GitHub 上叫 "Nav" 的仓库成千上万，
   直接按标题搜很容易挂错仓库。所以本模块的匹配规则**偏保守**：
   宁可漏，不可错 —— 挂错仓库比没有代码链接更糟。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124 Safari/537.36")
POLITE = "1035534180@qq.com"

# 从文本里捞 URL
_URL_RE = re.compile(r"https?://[^\s<>\"'\),\]}]+", re.I)

# 明显的"不是项目页"的域名（出版社、数据库、社交等）
_NOT_PROJECT = (
    "doi.org", "arxiv.org", "openalex.org", "crossref.org", "zenodo.org",
    "springer", "ieee.org", "sciencedirect", "wiley", "tandfonline",
    "mdpi.com", "frontiersin", "nature.com", "acm.org", "usenix",
    "researchgate", "semanticscholar", "orcid.org", "pubmed",
    "twitter.com", "x.com", "linkedin", "facebook", "youtube.com",
    "scholar.google", "dblp.org", "ssrn.com", "biorxiv", "medrxiv",
    "openreview.net", "proceedings.mlr.press", "jmlr.org",
    "creativecommons.org", "opensource.org", "w3.org",
    "aclweb.org", "aaai.org", "thecvf.com", "ieeexplore",
)

# 代码托管平台
_CODE_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org",
               "huggingface.co/spaces", "gitee.com", "sourceforge.net")

# 项目页常见特征（github.io 个人主页、项目站）
_PROJ_HINTS = ("project", "demo", "page", "site", "homepage", "video",
               "webpage", "results")


def extract_urls(text: str) -> list[str]:
    """从任意文本里提取 URL（去重、去尾部标点）"""
    if not text:
        return []
    out = []
    for m in _URL_RE.finditer(text):
        u = m.group(0).rstrip(".,;:!?)]}'\"")
        if u not in out:
            out.append(u)
    return out


def classify_url(url: str) -> str | None:
    """判断 URL 属于 project 还是 code；不属于则 None"""
    low = url.lower()
    if any(h in low for h in _CODE_HOSTS):
        # GitHub 仓库页（含 owner/repo），排除只到用户主页/组织页的链接
        m = re.match(r"https?://github\.com/([^/]+)/([^/?#]+)", low)
        if m and m.group(1) not in ("", "orgs", "topics", "search") \
                and m.group(2) not in ("", "?", "#"):
            return "code"
        return None
    if any(bad in low for bad in _NOT_PROJECT):
        return None
    # 看起来像项目主页：github.io 个人主页 / 含常见关键词
    if "github.io" in low or "pages." in low:
        return "project"
    if any(h in low for h in _PROJ_HINTS):
        return "project"
    return None


# "这是我们自己的代码/项目页"的强信号词（出现在 URL 前面）
_OWN_STRONG = (
    "our code", "our project", "we release", "we will release",
    "code is available", "code will be available", "code and data are available",
    "project page", "project website", "project site", "our implementation",
    "code at", "codebase", "we open-source", "we open source",
    "available at", "released at", "see our",
)
# 反向信号：明确是"我们在别人的基础上做"
_THIRD_PARTY = ("build on", "based on", "built upon", "we use", "e.g.", "such as",
                "following", "borrow", "pretrained from")


def _score_url(text: str, url: str) -> int:
    """按 URL 前文语境打分：在讲"我们自己的东西"得分高，在讲"别人的"得分低。

    动机：论文里常写"We build on https://github.com/facebook/segment-anything
    and release our code at https://github.com/ours/Nav" ——
    前者是别人的依赖，后者才是本文的代码，不能取错。
    """
    i = text.find(url)
    if i < 0:
        return 0
    window = text[max(0, i - 90):i].lower()
    score = 0
    for k in _OWN_STRONG:
        if k in window:
            score += 3
    for k in _THIRD_PARTY:
        if k in window:
            score -= 3
    return score


def from_text(*texts: str) -> dict:
    """从摘要 / comment 等文本里提取 project / code 链接（精度最高的一路）

    同一类型有多个候选时，取**语境得分最高**的那个。
    """
    out: dict[str, str] = {}
    best: dict[str, int] = {}
    for t in texts:
        t = t or ""
        for u in extract_urls(t):
            kind = classify_url(u)
            if not kind:
                continue
            sc = _score_url(t, u)
            if kind not in out or sc > best.get(kind, -999):
                out[kind] = u
                best[kind] = sc
    return out


# ══════════════════════════════════════════════════════════════
# GitHub 搜索（需要 PAT 才够配额；故意做保守匹配）
# ══════════════════════════════════════════════════════════════
_STOP = {"a", "an", "the", "for", "of", "in", "on", "with", "and", "to",
         "via", "using", "towards", "toward", "is", "are", "by", "from",
         "learning", "navigation", "navigating", "zero", "shot",
         "zeroshot", "training", "free", "trainingfree", "paper", "code",
         # 这些词在导航论文里几乎篇篇都有，用来匹配仓库名毫无区分度
         "robot", "robots", "robotic", "based", "language", "visual",
         "vision", "model", "models", "agent", "agents", "embodied",
         "environment", "environments", "task", "tasks", "method",
         "approach", "framework", "system", "systems", "study", "toward"}


def _sig_words(title: str, min_len: int = 5) -> set[str]:
    """取标题里的"特征词"（去掉通用词，用于匹配校验）

    min_len=5：实测 4 字符词（如 "nav", "vlm"）误命中率明显更高。
    """
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) >= min_len and w not in _STOP}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _title_head(title: str, n: int = 32) -> str:
    """标题开头的归一化片段 —— 用于和仓库描述比对。

    取 32 字符（而不是 14）：实测 14 字符片段太宽泛，
    像 "semantic inform" 这种在无关仓库描述里也会出现 → 误配。
    """
    t = _norm(title)
    return t[:n] if len(t) >= 12 else ""


def _tokens(s: str, min_len: int = 4) -> set[str]:
    """按词切分取 token（用于"仓库名 token 命中标题 token"的强信号）

    ⚠️ **必须同时过滤停用词**：只按长度过滤会漏进 "using" 这种词 ——
       实测它让一篇 VLM-Social-Nav 论文挂到了一个简历验证仓库上
       （因为仓库名里也有 "Using"）。
    """
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if len(w) >= min_len and w not in _STOP}


# 这些缩写太常见，不足以作为"系统名"证据（几乎每篇论文都可能有）
_COMMON_ACRONYMS = {
    "vlm", "vlms", "llm", "llms", "mllm", "ai", "rl", "uav", "uavs", "rgb",
    "rgbd", "slam", "gpt", "pdf", "json", "api", "cnn", "rnn", "nerf", "vit",
    "clip", "sam", "ai2", "sim", "real", "ios", "sota", "ode", "mcp", "sql",
    "dnn", "gan", "vae", "ppo", "dqn", "sac", "mpc", "ekf", "ukf", "imu",
    "gnss", "gps", "lidar", "rgbd", "cad", "ros", "cpu", "gpu", "tpu", "mlp",
    "vit", "bert", "ttt", "llm", "vla", "va", "vln", "vlns", "ocr", "asr",
}


def _system_names(title: str) -> set[str]:
    """从标题里提取**系统名候选**（缩写 / 驼峰命名）—— 最强的仓库匹配信号。

    动机：论文的官方仓库几乎都以**系统名**命名（因为那是项目的名字），
    而不是随便一个普通单词：
        "VLFM: Vision-Language Frontier Maps …"   → vlfm
        "L3MVN: Leveraging Large Language …"      → l3mvn
        "NavRL: Learning Safe Flight …"           → navrl
        "CoWs on Pasture: …"                      → cows

    提取规则（保守，两条都很关键）：
      · **全大写缩写**：无小写字母 + 含大写 + ≥3 字符（VLFM / L3MVN / HOZ）
      · **驼峰名**：**首个字符以外还有大写字母** + 含小写 + ≥4 字符（NavRL / CoWs）
      · 排除常见通用缩写（VLM/LLM/RL/UAV…）

    ⚠️ 为什么"驼峰"必须要求**内部**大写：
       标题里副标题的首词总是大写（"…: Socially Aware Robot…"），
       若只要求"首字母大写"就会把 "Socially" 当成系统名 ——
       实测这导致 VLM-Social-Nav 被挂到一个简历分析仓库上
       （那个仓库名里也有 "Social"）。
    """
    out = set()
    # 先按非字母数字切分（把 "VLM-Social-Nav" 拆成 VLM / Social / Nav）
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*", title or ""):
        if len(tok) < 3:
            continue
        has_lower = any(c.islower() for c in tok)
        has_upper = any(c.isupper() for c in tok)
        # 全大写缩写（允许含数字，如 L3MVN / VLFM2）
        is_acronym = has_upper and not has_lower and len(tok) >= 3
        # 驼峰：第 0 位之后还有大写（NavRL / CoWs / UniGoal）
        is_camel = (has_lower and has_upper
                    and any(c.isupper() for c in tok[1:]) and len(tok) >= 4)
        if not (is_acronym or is_camel):
            continue
        n = _norm(tok)
        if len(n) >= 3 and n not in _COMMON_ACRONYMS:
            out.add(n)
    return out


def github_code_url(title: str, token: str | None = None,
                    timeout: int = 20) -> str | None:
    """按论文标题在 GitHub 上找代码仓库。

    ⚠️ 保守策略 —— **挂错仓库比没有链接更糟**（读者会以为是官方实现）。
    打分制，必须 ≥5 分才采用：

      +8  仓库名含标题的**系统名**（如标题有 "VLFM" → 仓库 vlfm）← 最强
      +7  仓库描述里含**标题前 32 字符**（作者常把标题写进描述）  ← 次强
      +5  仓库名与标题有 **≥2 个**（已滤停用词的）词重合
      +4  仓库名（≥5 字符）是标题的子串
      ✗   描述像 "awesome / paper list" 的汇总仓库直接排除

    🐞 修过的三个 bug（都会误配，教训务必保留）：
      ① 拿"仓库名 vs 它自己的描述"做校验 —— 描述必然含自己名字，等于**必中**
         （VLM-Social-Nav → PPC-Account-Audit）
      ② 用 14 字符标题片段匹配描述 —— 太宽泛（Survey 论文 → 3DPlan）
      ③ token 匹配没滤停用词 / 只要求 1 个词命中 ——
         "using"、"social" 这种词会乱命中（VLM-Social-Nav → 简历分析仓库）
    """
    if not title:
        return None
    sig = sorted(_sig_words(title), key=len, reverse=True)[:5]
    if not sig:
        return None
    q = " ".join(sig[:4])
    url = ("https://api.github.com/search/repositories?"
           + urllib.parse.urlencode({"q": q, "sort": "stars",
                                     "order": "desc", "per_page": 8}))
    hdr = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception:  # noqa: BLE001  网络/配额问题都优雅跳过
        return None

    t_norm = _norm(title)
    t_tokens = _tokens(title)
    t_sig = _sig_words(title)
    head = _title_head(title)
    systems = _system_names(title)
    best: tuple[int, str] | None = None

    for item in d.get("items", [])[:8]:
        name = item.get("name", "") or ""
        desc = item.get("description") or ""
        if not name:
            continue

        dlow = desc.lower()
        if any(k in dlow for k in ("awesome", "reading list", "paper list",
                                   "collection of", "curated list")):
            continue

        name_norms = {_norm(x) for x in re.split(r"[-_.\s]+", name) if x}
        name_norms.discard("")

        score = 0
        # ① 系统名命中（最强）
        if systems and (name_norms & systems or _norm(name) in systems):
            score += 8
        # ② 标题前缀出现在仓库描述里
        if head and head in _norm(desc):
            score += 7
        # ③ ≥2 个实词重合
        if len(_tokens(name) & t_tokens) >= 2:
            score += 5
        # ④ 仓库名整体是标题子串
        n_norm = _norm(name)
        if len(n_norm) >= 5 and n_norm in t_norm:
            score += 4
        if len(_sig_words(name) & t_sig) >= 3:
            score += 3

        if score >= 5 and (best is None or score > best[0]):
            best = (score, item.get("html_url"))

    return best[1] if best else None


# ══════════════════════════════════════════════════════════════
# Unpaywall：开放获取 PDF（补 arXiv 之外的论文）
# ══════════════════════════════════════════════════════════════
def unpaywall_pdf(doi: str | None, email: str = POLITE,
                  timeout: int = 20) -> tuple[str | None, bool]:
    """返回 (pdf_url, is_oa)。失败时 (None, False)"""
    if not doi:
        return None, False
    doi = doi.strip().lower()
    if not doi.startswith("10."):
        return None, False
    url = (f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}"
           f"?email={email}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
    except Exception:  # noqa: BLE001
        return None, False
    oa = bool(d.get("is_oa"))
    best = d.get("best_oa_location") or {}
    pdf = best.get("url_for_pdf") or best.get("url")
    return (pdf or None), oa


# ══════════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 80)
    print("① URL 提取与分类")
    print("=" * 80)
    CASES = [
        ("See our project page: https://nav-project.github.io/ for videos.",
         "project", "https://nav-project.github.io/"),
        ("Code is available at https://github.com/foo/ZeroShotNav.",
         "code", "https://github.com/foo/ZeroShotNav"),
        ("Published in IEEE Xplore, doi:10.1109/LRA.2024.1 (see "
         "https://doi.org/10.1109/LRA.2024.1)", None, None),
        ("We build on https://github.com/facebookresearch/segment-anything "
         "and release code at https://github.com/bar/OurNav.",
         "code", "https://github.com/bar/OurNav"),
    ]
    ok = bad = 0
    for text, want_kind, want_url in CASES:
        got = from_text(text)
        if want_kind is None:
            good = want_kind not in got
        else:
            good = got.get(want_kind) == want_url
        ok += good
        bad += not good
        print(f"  {'✅' if good else '❌'} {text[:62]:64} → {got}")
        if not good:
            print(f"      期望 {want_kind}={want_url}")

    # ── 系统名提取（GitHub 匹配的核心强信号）
    print()
    print("=" * 80)
    print("② 系统名提取（防止把普通单词误当系统名）")
    print("=" * 80)
    SYS_CASES = [
        ("VLFM: Vision-Language Frontier Maps for Zero-Shot Semantic Navigation",
         True, "vlfm"),
        ("L3MVN: Leveraging Large Language Models for Visual Target Navigation",
         True, "l3mvn"),
        ("NavRL: Learning Safe Flight in Dynamic Environments", True, "navrl"),
        ("CoWs on Pasture: Baselines and Benchmarks", True, "cows"),
        ("HOZ++: Versatile Hierarchical Object-to-Zone Graph", True, "hoz"),
        # ⚠️ 下面这条是**回归测试**：VLM-Social-Nav 曾被误配到简历仓库，
        #    根因就是 "Socially"（副标题首词大写）被当成系统名。
        ("VLM-Social-Nav: Socially Aware Robot Navigation Through Scoring "
         "Using Vision-Language Models", False, None),
        ("Semantic Information for Robot Navigation: A Survey", False, None),
    ]
    for title, want_has, want_token in SYS_CASES:
        names = _system_names(title)
        if want_has:
            good = bool(names) and any(
                n.startswith(want_token) or w in n
                for n in names for w in [want_token])
        else:
            good = not names
        ok += good
        bad += not good
        print(f"  {'✅' if good else '❌'} {title[:58]:60} → {sorted(names)}")
        if not good:
            print(f"      期望 {'含 ' + want_token if want_has else '空集'}")

    print(f"\n{ok}/{ok+bad} 通过")
    raise SystemExit(0 if bad == 0 else 1)
