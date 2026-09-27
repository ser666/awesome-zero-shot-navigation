"""主题分类 —— 给每篇论文打「主分类」+「细粒度子专题标签」

设计说明
--------
Boss 要求参考 luohongkun.top/Embodied-AI-Daily 的做法：**细粒度子专题分类**，
让读者可以按方向深入（那个站有 ~45 个主题标签）。

所以这里做**两级**：
  ① primary  ：主分类（每篇唯一）→ README 用它分章节
  ② topics   ：子专题标签（每篇多个）→ 网站用它做 chips 筛选

分类靠「词边界匹配标题+摘要」，规则按**优先级从具体到宽泛**排列，
避免宽泛规则吃掉具体规则（例：Aerial VLN 必须先于 VLN 判定）。
"""

from __future__ import annotations

import re

# ══════════════════════════════════════════════════════════════
# 主分类（每篇唯一，按顺序判优 —— 越具体越靠前）
#
# ⚠️ 顺序是调过的，别随意改：
#   原先把 "Exploration / Semantic" 放在 ObjectNav 前面，结果 107 篇被判成
#   Exploration、ObjectNav 只剩 67 篇 —— 因为大量 ObjectNav 论文都会提到
#   "frontier-based exploration" 或 "semantic map"。
#   现在原则是：**先按"任务目标"判（aerial/multi-object/image-goal/social/
#   objectnav/vln），再用"能力/方法"兜底（semantic/exploration/LLM）**。
# ══════════════════════════════════════════════════════════════
PRIMARY_RULES: list[tuple[str, str]] = [
    # 空中 / 无人机（要在通用 VLN 之前，否则会被 VLN 吃掉）
    ("Aerial VLN",
     r"aerial|uav|drone|unmanned aerial|quadrotor|aircraft|flying|"
     r"terrestrial-aerial|air-ground"),
    # 多目标导航（比单目标更具体）
    ("Multi-Object Navigation",
     r"multi[- ]object navigat|multi[- ]target navigat|"
     r"object sequence|semantic target sequence|\bgoat\b"),
    # 图像 / 点目标导航
    ("Image & Point-Goal Navigation",
     r"image[- ]goal|image goal|point[- ]goal|goal image|"
     r"visual goal|goal[- ]conditioned|instance[- ]goal|instance navigation|"
     r"object[- ]path graph"),
    # 社会导航
    ("Social Navigation",
     r"social(ly)?[- ]?(compliant|aware)?\s*navigat|pedestrian|crowd|"
     r"human-aware navigation|socially aware|social robot"),
    # 目标导向导航（核心任务之一）
    ("Object-Goal Navigation (ObjectNav)",
     r"object[- ]goal|object[- ]?nav|goal[- ]object|"
     r"object goal navigat|target object navigat|"
     r"zero[- ]shot object navigat|semantic object navigat"),
    # 指令型 VLN（核心任务之二）
    ("Vision-and-Language Navigation (VLN)",
     r"\bvln\b|vision[- ]and[- ]language navigat|vision[- ]language navigat|"
     r"language[- ]guided navigat|instruction[- ]following|"
     r"navigat\w* instruction|r2r|rxr|touchdown|"
     r"talk2nav|dialogue navigat|conversational navigat"),
    # 开放词汇 / 语义导航（能力维度，放在任务之后兜底）
    ("Semantic & Open-Vocabulary Navigation",
     r"open[- ]?vocab|semantic navigation|semantic map|open[- ]?set|"
     r"zero[- ]shot semantic|category[- ]?agnostic"),
    # 探索（无明确目标，主动探索）
    ("Exploration",
     r"\bexploration\b|frontier[- ]based|active exploration|"
     r"exploration policy|map exploration|autonomous exploration"),
    # LLM/VLM 智能体式导航（方法维度，最后兜底）
    ("LLM / VLM Navigation Agents",
     r"\bllm\b|\bvlm\b|\bmllm\b|\bvla\b|large language model|"
     r"vision[- ]language model|multimodal large language|"
     r"foundation model|gpt-?[45]|react agent|embodied agent|"
     r"agentic navigat|llm[- ]driven|llm[- ]based|vision[- ]language[- ]action"),
]

# ══════════════════════════════════════════════════════════════
# 子专题标签（每篇多个）—— 网站 chips 用
#   (标签, 正则, 类别分组)
# ══════════════════════════════════════════════════════════════
TOPIC_RULES: list[tuple[str, str, str]] = [
    # ── 目标 / 任务类型 ──────────────────────────────
    ("ObjectNav", r"object[- ]goal|object[- ]?nav\b|goal object", "task"),
    ("InstanceNav", r"instance[- ]goal|instance navigat", "task"),
    ("ImageNav", r"image[- ]goal|goal image|image goal", "task"),
    ("PointNav", r"point[- ]goal|point goal", "task"),
    ("VLN", r"\bvln\b|vision[- ]and[- ]language navigat", "task"),
    ("VLN-CE", r"vln[- ]ce|r2r[- ]ce|continuous environment", "task"),
    ("Aerial", r"aerial|uav|drone|quadrotor", "task"),
    ("SocialNav", r"social(ly)?\s*(compliant|aware)?\s*navigat|pedestrian",
     "task"),
    ("Exploration", r"\bexploration\b|frontier", "task"),
    ("MultiObject", r"multi[- ]object navigat|multi[- ]target", "task"),
    ("AudioGoal", r"audio[- ]goal|sound[- ]?based navigat|acoustic navigat",
     "task"),
    ("EQA / Dialogue", r"embodied question answering|\beqa\b|dialogue navigat|"
                       r"conversational navigat|interactive navigat", "task"),
    ("Manipulation-Nav", r"navigat\w* among movable|movable obstacle|"
                         r"manipulation[- ]?aware|mobile manipulat", "task"),

    # ── 方法 / 技术 ─────────────────────────────────
    ("Zero-Shot", r"zero[- ]shot", "method"),
    ("Training-Free", r"training[- ]free|without (any )?training|"
                      r"no training required|trainingless", "method"),
    ("Open-Vocabulary", r"open[- ]?vocab", "method"),
    ("LLM-based", r"\bllm\b|large language model|gpt-?[45]|\bchatgpt\b",
     "method"),
    ("VLM / MLLM", r"\bvlm\b|\bmllm\b|vision[- ]language model|"
                   r"multimodal large language", "method"),
    ("VLA", r"\bvla\b|vision[- ]language[- ]action", "method"),
    ("Foundation Model", r"foundation model|\bsam\b|\bclip\b|grounding dino|"
                         r"segment anything", "method"),
    ("Memory", r"\bmemory\b|memoriz|revisit|episodic|long[- ]term memory",
     "method"),
    ("Semantic Map", r"semantic map|semantic mapping|scene graph|"
                     r"topological (map|graph|representation)|voxel map|"
                     r"occupancy (map|grid)", "method"),
    ("Diffusion / Generative",
     r"diffusion (model|policy|planner)|generative (model|planner)|"
     r"video (generation|imagination|prediction)|world model|"
     r"\bgan\b|latent (space )?planning", "method"),
    ("Reinforcement Learning",
     r"reinforcement learning|\brl\b|\bppo\b|\bsac\b|actor[- ]critic|"
     r"\bdqn\b|reward shaping", "method"),
    ("Reasoning", r"chain[- ]of[- ]thought|\bcot\b|reasoning|react\b|"
                  r"commonsense|thinking|planning module", "method"),
    ("Graph-based", r"graph neural|\bgnn\b|knowledge graph|graph reasoning|"
                    r"scene graph", "method"),
    ("Prompting", r"prompt(ing)?|in[- ]context learning|few[- ]shot",
     "method"),
    ("Self-Supervised", r"self[- ]supervised|contrastive|pretrain", "method"),
    ("Active Perception", r"active perception|active vision|next[- ]best[- ]view|"
                          r"active (slam|exploration|mapping)", "method"),

    # ── 环境 / 平台 ─────────────────────────────────
    ("Simulation", r"habitat|ai2[- ]?thor|robothor|\bsimulat|matterport|"
                   r"proc[- ]?thor|gibson|isaac (sim|gym)|gazebo|minecraft|"
                   r"carla|unreal|webots", "env"),
    ("Real Robot", r"real[- ]world|real robot|physical robot|deployed on|"
                   r"mobile robot experiment|jackal|turtlebot|unitree|"
                   r"quadruped|drone flight|on a real", "env"),
    ("Outdoor", r"outdoor|off[- ]road|field robot|urban|street|wilderness|"
                r"agricultur|forest", "env"),
    ("Indoor", r"indoor|household|home environment|apartment|office|"
               r"room[- ]to[- ]room", "env"),

    # ── 任务性质 ────────────────────────────────────
    ("Benchmark", r"benchmark|\bdataset\b|leaderboard|evaluation suite|"
                  r"testbed|challenge", "nature"),
    ("Survey", r"survey|review|taxonomy|overview of|comprehensive study",
     "nature"),
    ("Lifelong / Continual",
     r"lifelong|continual learning|incremental|catastrophic forgetting",
     "nature"),
    ("Efficiency", r"real[- ]time|efficient|edge (gpu|device)|lightweight|"
                   r"latency|on[- ]device|acceleration", "nature"),
    ("Safety", r"safe(ty)?[- ](critical|aware|planning)|risk[- ]aware|"
               r"collision avoidance|guarantee", "nature"),
]

# （词边界匹配由 _compile 统一处理）


def _compile(pattern: str) -> re.Pattern:
    """词边界匹配编译（避免 'nav' 命中 'navigation' 之类的子串误命中）"""
    return re.compile(pattern, re.I)


PRIMARY_COMPILED = [(name, _compile(pat)) for name, pat in PRIMARY_RULES]
TOPIC_COMPILED = [(name, _compile(pat), grp) for name, pat, grp in TOPIC_RULES]


def primary_category(title: str, abstract: str = "") -> str:
    """判定主分类（命中第一条即返回；都不中则 Other）"""
    text = f"{title} {(abstract or '')[:1200]}"
    for name, rx in PRIMARY_COMPILED:
        if rx.search(text):
            return name
    return "Other"


def topic_tags(title: str, abstract: str = "", venue_kind: str = "") -> list[str]:
    """打子专题标签（多标签）"""
    text = f"{title} {(abstract or '')[:2000]}"
    out = []
    for name, rx, _grp in TOPIC_COMPILED:
        if rx.search(text):
            out.append(name)
    return out


# 网站 chips 的展示顺序（按组）
CATEGORY_ORDER = [
    "Object-Goal Navigation (ObjectNav)",
    "Vision-and-Language Navigation (VLN)",
    "Aerial VLN",
    "Semantic & Open-Vocabulary Navigation",
    "Image & Point-Goal Navigation",
    "Multi-Object Navigation",
    "Social Navigation",
    "Exploration",
    "LLM / VLM Navigation Agents",
    "Other",
]

TOPIC_GROUPS = {
    "task": "🎯 任务类型",
    "method": "🔬 方法",
    "env": "🏠 环境",
    "nature": "📌 性质",
}


# ══════════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    CASES = [
        # (标题, 摘要片段, 期望主分类)
        ("Zero-Shot Object Navigation with Vision-Language Models",
         "We address object-goal navigation in unseen scenes.",
         "Object-Goal Navigation (ObjectNav)"),
        ("Zero-Shot Vision-and-Language Navigation in Continuous Environments",
         "R2R-CE benchmark, instruction following.",
         "Vision-and-Language Navigation (VLN)"),
        ("AirVLN: Zero-Shot Aerial Vision-and-Language Navigation",
         "UAV drone navigation from language instructions.",
         "Aerial VLN"),
        ("Open-Vocabulary Semantic Navigation with Frontier Scoring",
         "We build a semantic map and use open-vocabulary perception.",
         "Semantic & Open-Vocabulary Navigation"),
        ("Image-Goal Navigation via Latent World Models",
         "Given a goal image, the agent navigates to it.",
         "Image & Point-Goal Navigation"),
        ("Socially Compliant Navigation in Dynamic Pedestrian Environments",
         "Pedestrian-rich environments, social awareness.",
         "Social Navigation"),
        ("Autonomous Exploration with Frontier-Based Planning",
         "We propose an exploration policy for unknown environments.",
         "Exploration"),
        ("GPT-Driven Robot Navigation Reasoning",
         "We use an LLM to reason about the scene.",
         "LLM / VLM Navigation Agents"),
        ("A Survey of Protein Folding", "biology", "Other"),
        ("Multi-Object Navigation with Semantic Target Sequences",
         "multi-object navigation tasks", "Multi-Object Navigation"),
    ]
    ok = bad = 0
    print("=" * 80)
    print("主分类自测")
    print("=" * 80)
    for title, ab, want in CASES:
        got = primary_category(title, ab)
        good = got == want
        ok += good
        bad += not good
        print(f"  {'✅' if good else '❌'} {title[:58]:60} → {got}")
        if not good:
            print(f"      期望: {want}")

    print()
    print("=" * 80)
    print("子专题标签样本")
    print("=" * 80)
    for title, ab, _ in CASES[:5]:
        tags = topic_tags(title, ab)
        print(f"  {title[:52]:54} → {', '.join(tags)}")

    print(f"\n主分类 {ok}/{ok+bad} 通过")
    raise SystemExit(0 if bad == 0 else 1)
