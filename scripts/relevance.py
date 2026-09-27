"""相关性过滤 — 判定论文是否属于「零样本导航」方向

设计原则
--------
**核心判据**：论文必须同时满足
  ① 关于"具身智能体在物理空间中的导航"（不是参数空间/网页/图表导航）
  ② 无需任务特定训练（zero-shot / training-free / open-vocabulary / 基础模型直接泛化）

关键设计取舍（踩过的坑）
------------------------
· **词边界匹配**（\\b）：早先用朴素子串匹配时，`gui` 命中 `guided`、
  `clip` 命中 `clipping`、`nav` 命中一切 —— 造成大量误收。
· **导航词必须在标题里**（规则 A/B）：仅靠摘要出现 "navigate" 太松 ——
  宇宙学论文会写 "navigate the parameter space"，被误判成导航研究。
· **摘要里的 nav 密度**只用于规则 C 兜底，且必须叠加零样本 + 具身语境。

判定规则（命中任一即相关）
  A) 标题含导航词 且 含零样本词
  B) 标题含导航词 且 含具身/机器人/仿真词 且 含大模型/基础模型/VLA 词
  C) 摘要导航密度高（≥2）且 零样本 且 具身  （标题无导航词时的兜底）

自测：python3 scripts/relevance.py
"""

from __future__ import annotations

import re

# ── 强导航词（标题里出现即认为是在做导航任务）
NAV_STRONG = [
    "navigation", "navigator", "navigate", "navigating", "navigational",
    "objectnav", "object navigation", "object-goal navigation",
    "object goal navigation", "goalnav",
    "goal navigation", "goal-oriented navigation", "goal-directed navigation",
    "embodied navigation", "robot navigation", "mobile robot navigation",
    "indoor navigation", "aerial navigation", "uav navigation",
    "visual navigation", "semantic navigation", "social navigation",
    "vln", "r2r", "rxr", "reverie", "touchdown", "goat-bench",
    "hm3d-ovon", "ovon", "instru-nav",
    "instruction following", "instruction-following",
    "point-goal navigation", "point goal navigation",
    "language-guided navigation", "language guided navigation",
    "vision-and-language navigation", "vision-and-language",
    "vision language navigation", "vision-language navigation",
]

# 弱导航词（需更强的上下文配合；用于兜底）
NAV_WEAK = [
    "waypoint", "frontier", "exploration", "path planning", "route planning",
    "localization", "slam", "trajectory planning", "goal-reaching",
    "goal reaching", "embodied agent",
]

# 零样本 / 免训练 / 开放词表
ZS_TERMS = [
    "zero-shot", "zero shot", "zeroshot", "training-free", "training free",
    "trainingless", "without training", "no training", "requires no training",
    "without fine-tuning", "no fine-tuning", "without any training",
    "without additional training", "without task-specific training",
    "tuning-free", "finetuning-free",
    "open-vocabulary", "open vocabulary", "open-vocab",
    "unseen object", "unseen goal", "unseen environment",
    "generalize to novel", "generalization to novel",
    "without any task-specific", "plug-and-play",
]

# 具身 / 机器人 / 仿真
EMBODIED_TERMS = [
    "robot", "robotic", "robotics", "embodied", "embodiment", "agent",
    "uav", "drone", "quadruped", "legged robot", "manipulator",
    "habitat", "matterport", "hm3d", "gibson", "ai2-thor", "robothor",
    "mobile robot", "sim-to-real", "household", "simulator", "simulation",
    "physical agent", "embodied ai",
]

# 大模型 / 基础模型 / VLA
LLM_TERMS = [
    "llm", "large language model", "large language models",
    "vlm", "vision-language model", "vision-language models",
    "mllm", "multimodal large language", "foundation model",
    "foundation models", "vla", "vision-language-action",
    "vision language action", "gpt-4", "gpt-4v", "gpt4", "clip",
    "llava", "multimodal model", "pretrained model", "large vision-language",
    "semantic map", "semantic mapping", "scene graph", "open-vocabulary model",
]

# ── 无关领域（命中即排除）—— 注意用词边界，避免误杀
NEGATIVE_TERMS = [
    # 医学 / 生物
    "surgery", "surgical", "guidewire", "biomedical", "pubmed", "clinical",
    "patient", "cancer", "tumor",
    # 遥感 / 地理 / 天文
    "remote sensing", "satellite image", "land cover", "cosmology",
    "cosmological", "astrophysical", "parameter space", "galaxy",
    "geographic information system",
    # 化学 / 材料
    "drug discovery", "protein structure", "genomic", "molecular dynamics",
    "crystal structure", "catalyst",
    # GUI / 网页 / 代码（"navigation" 的高频误匹配）
    "gui navigation", "website navigation", "web navigation",
    "hypertext", "menu navigation", "code navigation",
    "repository navigation", "file navigation", "android navigation",
    "app navigation", "browser navigation",
    # 语言 / 翻译
    "zero-shot translation", "machine translation", "translation quality",
    "bilingual corpus", "simultaneous translation", "language tags",
    # 通信 / 网络
    "wireless sensor network", "vehicular network", "packet routing",
    "network routing", "digital twin", "router",
    # 视觉任务（非导航）
    "instance segmentation", "multi-object tracking", "object tracking",
    "gesture generation", "light field", "image restoration",
    "image super-resolution", "pose estimation", "re-identification",
    "image classification", "image captioning", "video captioning",
    "text-to-image", "text-to-video", "image generation", "face",
    "speech recognition", "recommendation system",
    # 其他
    "hydrodynamic", "text-to-svg", "svg generation", "font generation",
    "pilot licence", "pilot license", "underwater target tracking",
    "evolutionary policy search", "curriculum learning for",
    "benchmark for proactive",
]

# 数据集/勘误类（非研究论文）
DROP_TITLE_PREFIXES = [
    "dataset for:", "dataset:", "correction to", "erratum", "retraction",
    "author correction", "publisher correction", "editorial",
]

# 标题含这些词且无强导航词时排除
OFFDOMAIN_TITLE = [
    "manipulation", "grasping", "object counting", "counting",
    "sentiment", "pornographic", "recommendation",
]


def _compile(terms):
    """把词表编译成词边界正则（避免子串误匹配）"""
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"(?<![a-z0-9])(?:" + "|".join(escaped) + r")(?![a-z0-9])")


_RE_STRONG = _compile(NAV_STRONG)
_RE_WEAK = _compile(NAV_WEAK)
_RE_ZS = _compile(ZS_TERMS)
_RE_EMB = _compile(EMBODIED_TERMS)
_RE_LLM = _compile(LLM_TERMS)
_RE_NEG = _compile(NEGATIVE_TERMS)
_RE_OFF = _compile(OFFDOMAIN_TITLE)
# 导航密度统计：任何 navig* 词形 + VLN 类缩写
_RE_NAVDENSE = re.compile(r"(?<![a-z0-9])(?:navig\w*|navigat\w*|vln|objectnav|"
                          r"goalnav|ovon)(?![a-z0-9])")


def _norm(text) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def check(title: str = "", abstract: str = "") -> tuple:
    """返回 (是否相关, 命中的规则/原因)"""
    t = _norm(title)
    a = _norm(abstract)
    if not (t or a).strip():
        return False, "empty"

    # 0) 数据集 / 勘误
    if any(t.startswith(p) for p in DROP_TITLE_PREFIXES):
        return False, "dataset-or-erratum"

    # 1) 无关领域（标题优先，摘要也查）
    if _RE_NEG.search(t):
        return False, "negative-domain-title"
    if _RE_NEG.search(a):
        return False, "negative-domain-abstract"

    title_nav = bool(_RE_STRONG.search(t))
    title_weak = bool(_RE_WEAK.search(t))
    zs = bool(_RE_ZS.search(t) or _RE_ZS.search(a))
    emb = bool(_RE_EMB.search(t) or _RE_EMB.search(a))
    llm = bool(_RE_LLM.search(t) or _RE_LLM.search(a))
    nav_dense = len(_RE_NAVDENSE.findall(f"{t} {a}"))

    # 2) 标题是别的任务且没有强导航词 → 排除
    if _RE_OFF.search(t) and not title_nav:
        return False, "offdomain-title"

    # 3) 规则 A：标题含导航词 + 零样本
    if title_nav and zs:
        return True, "A:title-nav+zeroshot"

    # 4) 规则 B：标题含导航词 + 具身语境 + 大模型/基础模型/VLA
    if title_nav and emb and llm:
        return True, "B:title-nav+embodied+llm"

    # 5) 规则 B'：标题含导航词 + 具身 + 摘要导航密集（有些基础模型论文不提 LLM 缩写）
    if title_nav and emb and nav_dense >= 3:
        return True, "B2:title-nav+embodied+dense"

    # 6) 规则 C：标题无导航词 → 兜底（需摘要导航密集 + 零样本 + 具身）
    if nav_dense >= 2 and zs and emb:
        return True, "C:abs-nav-dense+zs+embodied"

    # 7) 规则 C'：弱导航词 + 标题具身 + 零样本
    if title_weak and _RE_EMB.search(t) and zs:
        return True, "C2:weak-nav+title-embodied+zs"

    if title_nav:
        return False, "title-nav-but-no-zs-context"
    return False, "no-nav"


def is_relevant(paper) -> bool:
    if isinstance(paper, dict):
        return check(paper.get("title", ""), paper.get("abstract", ""))[0]
    return check(getattr(paper, "title", "") or "",
                 getattr(paper, "abstract", "") or "")[0]


# ══════════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════════
TESTS = [
    # ── 应通过
    ("Zero-Shot Object Navigation with Vision-Language Models",
     "We propose a training-free method for object goal navigation.", True),
    ("TANGO: Humanoid Navigation in Cluttered Environments with a VLA Model",
     "We present a vision-language-action model controlling a humanoid robot "
     "for navigation.", True),
    ("AirAnchor: Zero-Shot Aerial Vision-and-Language Navigation",
     "Aerial VLN requires drones to follow instructions in unseen environments.", True),
    ("NavGPT: Explicit Reasoning in Vision-and-Language Navigation with LLMs",
     "We leverage a large language model for navigation in embodied environments.", True),
    ("Open-Vocabulary Object Goal Navigation with a Foundation Model",
     "Our robot navigates to unseen object categories in Habitat.", True),
    # ── 应拒绝（早期版本误收的真实案例）
    ("Machine Translation with Zero-Shot Transfer",
     "A study on language tags for zero-shot translation.", False),
    ("Lava Void Cosmology Pillar 22: A Valediction and Invitation",
     "We navigate the cosmological parameter space with LLM agents.", False),
    ("Open-Vocabulary Multi-Object Tracking Based on Multi-Cue Fusion",
     "Tracking objects with an open vocabulary robot.", False),
    ("CDIS: Cross-Dimensional Class-Agnostic 3D Instance Segmentation",
     "Instance segmentation with zero-shot open vocabulary.", False),
    ("EmoPose: Vision-Language Model Guided Emotion-Aware Gesture Generation",
     "A VLM guides gesture generation for a virtual human.", False),
    ("Web Navigation with Language Models",
     "Modeling website navigation and menu navigation for agents.", False),
    ("Asymmetric physics enables efficient learning in quadrupedal robot swarms",
     "We study quadruped robot swarms with zero-shot transfer.", False),
    ("Image Classification with ResNet", "A benchmark on ImageNet.", False),
    ("Data-Driven Estimation of Ship Manoeuvring Hydrodynamic Derivatives",
     "We estimate hydrodynamic derivatives with machine learning.", False),
    ("Vision-Reasoning-Guided Occlusion Removal from Light Fields",
     "A foundation model removes occlusions in light fields.", False),
]


def _selftest():
    ok = 0
    fails = []
    for title, abs_, expect in TESTS:
        got, why = check(title, abs_)
        if got == expect:
            ok += 1
        else:
            fails.append((expect, got, why, title))
    print(f"{ok}/{len(TESTS)} passed")
    if fails:
        print("\n未通过：")
        for exp, got, why, title in fails:
            print(f"  期望={exp} 实际={got} [{why}]  {title[:70]}")
    return ok == len(TESTS)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
