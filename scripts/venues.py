"""会议 / 期刊归一化与分级 —— 让 README 能显示「年份 + 加粗的重要会议名」

用途
----
数据源（OpenAlex / Crossref）给的 venue 是**很长的全名**，例如：
    "2025 IEEE International Conference on Robotics and Automation (ICRA)"
    "Proceedings of the AAAI Conference on Artificial Intelligence"
    "IEEE Transactions on Pattern Analysis and Machine Intelligence"
README 里显示全名会非常臃肿，所以需要：
    ① 归一化成常用简称（ICRA / AAAI / TPAMI）
    ② 判断级别（CCF-A / 机器人顶会/顶刊），只给"重要"的加粗
    ③ 解析出年份

分级口径（Boss 要求：**CCF-A 或机器人领域主要会议期刊**）
--------------------------------------------------------
tier="A"    → 领域公认顶级（CCF-A + 机器人顶会/顶刊）
tier="B"    → 有一定影响力（用于"次要加粗"，目前不启用）
tier=None   → 普通期刊 / 预印本

⚠️ 关于 ICLR / ECCV / RSS / CoRL：
   它们不在 CCF 官方列表里（品牌新或 CCF 未收录），但学界公认属顶级，
   按 Boss 的意图（"重点的会议或者期刊"）一并计入 A 级。
"""

from __future__ import annotations

import re

# ══════════════════════════════════════════════════════════════
# 归一化表：(匹配模式, 简称, 级别, 类型)
#   · 模式用【不区分大小写】正则、按顺序匹配，先精确后模糊
#   · 越具体的规则要放越前面（否则会被泛化规则吃掉）
# ══════════════════════════════════════════════════════════════
VENUE_RULES: list[tuple[str, str, str | None, str]] = [
    # ── 计算机视觉（CCF-A）───────────────────────────────
    (r"\bCVPR\b|Conference on Computer Vision and Pattern Recognition",
     "CVPR", "A", "conference"),
    (r"\bICCV\b|International Conference on Computer Vision\b",
     "ICCV", "A", "conference"),
    (r"\bECCV\b|European Conference on Computer Vision",
     "ECCV", "A", "conference"),          # CCF-B，但 CV 三大顶会之一
    (r"\bWACV\b|Winter Conference on Applications of Computer Vision",
     "WACV", "B", "conference"),
    (r"\bBMVC\b|British Machine Vision Conference",
     "BMVC", "B", "conference"),

    # ── 机器学习 / AI（CCF-A 及同级）──────────────────────
    (r"\bNeurIPS\b|\bNIPS\b|Neural Information Processing Systems",
     "NeurIPS", "A", "conference"),
    (r"\bICML\b|International Conference on Machine Learning",
     "ICML", "A", "conference"),
    (r"\bICLR\b|International Conference on Learning Representations",
     "ICLR", "A", "conference"),
    (r"\bAAAI\b|AAAI Conference on Artificial Intelligence",
     "AAAI", "A", "conference"),
    (r"\bIJCAI\b|International Joint Conference on Artificial Intelligence",
     "IJCAI", "A", "conference"),

    # ── NLP / 多媒体 / 信息检索（CCF-A）───────────────────
    (r"\bACL\b|Annual Meeting of the Association for Computational Linguistics",
     "ACL", "A", "conference"),
    (r"\bEMNLP\b|Empirical Methods in Natural Language Processing",
     "EMNLP", "B", "conference"),
    (r"\bNAACL\b", "NAACL", "B", "conference"),
    (r"\bACM MM\b|ACM International Conference on Multimedia",
     "ACM MM", "A", "conference"),
    (r"\bSIGGRAPH\b", "SIGGRAPH", "A", "conference"),
    (r"\bSIGIR\b", "SIGIR", "A", "conference"),
    (r"\bKDD\b|Knowledge Discovery and Data Mining", "KDD", "A", "conference"),
    (r"\bWWW\b|The Web Conference", "WWW", "A", "conference"),

    # ── 机器人领域主要会议（Boss 明确要求）─────────────────
    (r"\bRSS\b|Robotics:?\s*Science and Systems", "RSS", "A", "conference"),
    (r"\bCoRL\b|Conference on Robot Learning", "CoRL", "A", "conference"),
    (r"\bICRA\b|International Conference on Robotics and Automation",
     "ICRA", "A", "conference"),
    (r"\bIROS\b|International Conference on Intelligent Robots and Systems",
     "IROS", "A", "conference"),
    (r"\bICAPS\b|International Conference on Automated Planning and Scheduling",
     "ICAPS", "A", "conference"),
    (r"\bISRR\b|International Symposium on Robotics Research",
     "ISRR", "A", "conference"),
    (r"\bHumanoids\b|IEEE-RAS International Conference on Humanoid Robots",
     "Humanoids", "B", "conference"),
    (r"\bCASE\b|Conference on Automation Science and Engineering",
     "CASE", "B", "conference"),
    (r"\bIV\b|Intelligent Vehicles Symposium", "IV", "B", "conference"),
    (r"\b3DV\b|International Conference on 3D Vision", "3DV", "B", "conference"),

    # ── 顶级期刊 ─────────────────────────────────────────
    (r"Transactions on Pattern Analysis and Machine Intelligence|\bTPAMI\b",
     "TPAMI", "A", "journal"),            # CCF-A
    (r"International Journal of Computer Vision|\bIJCV\b",
     "IJCV", "A", "journal"),             # CCF-A
    (r"Transactions on Image Processing|\bTIP\b",
     "TIP", "A", "journal"),              # CCF-A
    (r"Journal of Machine Learning Research|\bJMLR\b",
     "JMLR", "A", "journal"),             # CCF-A
    (r"\bArtificial Intelligence\b(?!.*conference)", "AIJ", "A", "journal"),
    (r"Transactions on Knowledge and Data Engineering|\bTKDE\b",
     "TKDE", "A", "journal"),
    (r"Science Robotics", "Science Robotics", "A", "journal"),
    (r"International Journal of Robotics Research|\bIJRR\b",
     "IJRR", "A", "journal"),
    (r"Transactions on Robotics\b|\bT-RO\b|\bTRO\b",
     "T-RO", "A", "journal"),
    (r"Robotics and Automation Letters|\bRA-?L\b",
     "RA-L", "B", "journal"),             # CCF-B，但机器人圈主力刊
    (r"Transactions on Neural Networks and Learning Systems|\bTNNLS\b",
     "TNNLS", "B", "journal"),            # CCF-B
    (r"Transactions on Circuits and Systems for Video Technology|\bTCSVT\b",
     "TCSVT", "B", "journal"),
    (r"Transactions on Instrumentation and Measurement|\bTIM\b",
     "TIM", "B", "journal"),
    (r"Journal of Field Robotics|\bJFR\b", "JFR", "B", "journal"),
    (r"Autonomous Robots\b", "AURO", "B", "journal"),
    (r"Pattern Recognition\b(?!.*conference)", "PR", "B", "journal"),
    (r"Neural Networks\b", "Neural Networks", "B", "journal"),
    (r"Neurocomputing\b", "Neurocomputing", "B", "journal"),
    (r"Expert Systems with Applications", "ESWA", "B", "journal"),
    (r"Knowledge-Based Systems", "KBS", "B", "journal"),
    (r"Information Fusion", "Inf. Fusion", "A", "journal"),  # CCF-A
    (r"Transactions on Multimedia|\bTMM\b", "TMM", "B", "journal"),

    # ── 预印本 / 其它（明确标出，避免被误认为正式发表）────────
    (r"arXiv|Cornell University", "arXiv", None, "preprint"),
    (r"Zenodo", "Zenodo", None, "preprint"),
    (r"SSRN", "SSRN", None, "preprint"),
    (r"bioRxiv|medRxiv", "bioRxiv", None, "preprint"),
    (r"OpenReview", "OpenReview", None, "preprint"),
    (r"Research Square", "Research Sq.", None, "preprint"),
    (r"Underline Science", "Underline", None, "other"),
    (r"Lecture [Nn]otes in Computer Science|LNCS",
     "LNCS", None, "proceedings"),
    (r"\bSensors\b", "Sensors", None, "journal"),
    (r"\bElectronics\b", "Electronics", None, "journal"),
    (r"Applied Sciences", "Appl. Sci.", None, "journal"),
    (r"\bDrones\b", "Drones", None, "journal"),
    (r"IEEE Access\b", "IEEE Access", None, "journal"),
    (r"\bMathematics\b", "Mathematics", None, "journal"),
    (r"Journal of Navigation", "J. Navigation", None, "journal"),
    (r"Journal of Intelligent & Robotic Systems|\bJINT\b",
     "JINT", "B", "journal"),
    (r"Robotics and Autonomous Systems|\bRAS\b", "RAS", "B", "journal"),
]

# 年份提取：优先 "2025 IEEE ... (ICRA)" 这种前缀，其次 "(ICRA 2025)"、后缀 "2019"
_YEAR_PREFIX = re.compile(r"^\s*(?:Proceedings of (?:the )?)?((?:19|20)\d{2})\b")
_YEAR_ANY = re.compile(r"\b((?:19|20)\d{2})\b")
# 括号里的简称，如 "...(ICRA)"
_PAREN = re.compile(r"\(([A-Za-z][A-Za-z0-9\-/&\. ]{1,28})\)")

# 值不值得单独显示（这些"期刊"名对读者无信息量）
NOISE_VENUES = {
    "", "arxiv", "zenodo", "ssrn", "underline", "research sq.",
    "lncs", "openreview",
}


def normalize(venue: str | None, fallback_year: int | None = None
              ) -> tuple[str, str | None, str, int | None]:
    """把原始 venue 字符串归一化。

    返回 (简称, 级别, 类型, 年份)
      · 简称：如 "ICRA"；无法识别时返回清洗后的原名（截断）
      · 级别："A" / "B" / None
      · 类型："conference" / "journal" / "preprint" / "proceedings" / "other"
    """
    raw = (venue or "").strip()
    if not raw:
        return "", None, "unknown", fallback_year

    # 年份
    year = None
    m = _YEAR_PREFIX.search(raw)
    if m:
        year = int(m.group(1))
    else:
        m = _YEAR_ANY.search(raw)
        if m:
            year = int(m.group(1))
    if year is None:
        year = fallback_year

    # 规则匹配（先具体后泛化；规则表已按此排序）
    for pattern, short, tier, kind in VENUE_RULES:
        if re.search(pattern, raw, re.I):
            return short, tier, kind, year

    # 未命中：清洗后截断
    clean = re.sub(r"^(Proceedings of (the )?)", "", raw, flags=re.I)
    clean = re.sub(r"\s*\((?:19|20)\d{2}\)\s*$", "", clean).strip()
    if len(clean) > 46:
        clean = clean[:43] + "…"
    return clean, None, "journal", year


def is_notable(tier: str | None) -> bool:
    """是否属于"重要会议/期刊"（README 里要加粗）"""
    return tier == "A"


def display(short: str, year: int | None, tier: str | None,
            kind: str) -> str:
    """生成 README 里显示的 venue 文本（调用方负责加粗）。

    例子：
        ("ICRA", 2025, "A", "conference")      → "ICRA 2025"
        ("TPAMI", 2024, "A", "journal")        → "TPAMI 2024"
        ("arXiv", 2025, None, "preprint")      → "arXiv"      ← 不显示年份
        ("Sensors", 2026, None, "journal")     → "Sensors 2026"
    """
    if not short:
        return "arXiv" if year else "—"
    if kind == "preprint":
        return short                      # 预印本不加年份（年份已在条目开头）
    if year:
        return f"{short} {year}"
    return short


def badge_label(short: str, year: int | None) -> str:
    """给网站用的短标签"""
    if not short:
        return ""
    if year and short not in ("arXiv", "Zenodo", "SSRN", "OpenReview"):
        return f"{short} {year}"
    return short


# ══════════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    CASES = [
        # (原始 venue, 期望简称, 期望级别, 期望类型)
        ("2025 IEEE International Conference on Robotics and Automation (ICRA)",
         "ICRA", "A", "conference"),
        ("2024 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
         "IROS", "A", "conference"),
        ("Proceedings of the AAAI Conference on Artificial Intelligence",
         "AAAI", "A", "conference"),
        ("IEEE Transactions on Pattern Analysis and Machine Intelligence",
         "TPAMI", "A", "journal"),
        ("IEEE Robotics and Automation Letters", "RA-L", "B", "journal"),
        ("arXiv (Cornell University)", "arXiv", None, "preprint"),
        ("Zenodo (CERN European Organization for Nuclear Research)",
         "Zenodo", None, "preprint"),
        ("Lecture notes in computer science", "LNCS", None, "proceedings"),
        ("Sensors", "Sensors", None, "journal"),
        ("2023 IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)",
         "CVPR", "A", "conference"),
        ("Conference on Robot Learning (CoRL)", "CoRL", "A", "conference"),
        ("Robotics: Science and Systems (RSS)", "RSS", "A", "conference"),
        ("The International Journal of Robotics Research", "IJRR", "A", "journal"),
        ("IEEE Transactions on Robotics", "T-RO", "A", "journal"),
        ("Science Robotics", "Science Robotics", "A", "journal"),
        ("Neural Information Processing Systems", "NeurIPS", "A", "conference"),
        ("International Conference on Learning Representations",
         "ICLR", "A", "conference"),
        ("2026 IEEE International Conference on Robotics and Automation (ICRA)",
         "ICRA", "A", "conference"),
    ]
    ok = bad = 0
    print("=" * 78)
    print("venue 归一化自测")
    print("=" * 78)
    for raw, want_short, want_tier, want_kind in CASES:
        short, tier, kind, year = normalize(raw, fallback_year=2000)
        good = (short == want_short and tier == want_tier and kind == want_kind)
        ok += good
        bad += not good
        mark = "✅" if good else "❌"
        print(f"  {mark} {raw[:60]:62} → {short:16} tier={tier} "
              f"kind={kind} year={year}")
        if not good:
            print(f"      期望 {want_short} / {want_tier} / {want_kind}")
    print(f"\n{ok}/{ok+bad} 通过")
    raise SystemExit(0 if bad == 0 else 1)
