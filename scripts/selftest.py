#!/usr/bin/env python3
"""仓库级一致性自测 — 防"静默失效"类 bug

目前检查：
  ① keepalive.py 的 WORKFLOW 常量，必须对应一个**真实存在**的工作流文件
     （曾经残留已删除的 daily-update.yml → 月度看护静默失效）
  ② 工作流 YAML 里引用的 scripts/*.py，必须都真实存在
     （改了文件名忘了改 workflow → 云端跑起来才报错）
  ③ 工作流里引用 secrets 的地方，只允许白名单内的名字
     （防止写错 secret 名导致 token 装载失败且不报错）
  ④ ⭐ 提交数据的步骤必须先 pull --rebase 再 push
     （2026-09-30 实测：并发推送会让最后一步失败 ⇒ 整轮采集白跑）
  ⑤ ⭐ 自测步骤必须排在采集之前
     （坏规则若先写数据，会污染库 —— 比直接失败更难收拾）

不联网，秒级。用法：
    python3 scripts/selftest.py
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WF_DIR = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"

# 允许出现的 GitHub Secrets 名（新增请在此登记）
ALLOWED_SECRETS = {"GH_PAT", "GITHUB_TOKEN"}

ok = True


def fail(msg: str) -> None:
    global ok
    ok = False
    print(f"   ❌ {msg}")


def good(msg: str) -> None:
    print(f"   ✅ {msg}")


def step_block(text: str, name: str) -> str | None:
    """取出某个 step 的源文本（从 `- name: X` 到下一个同级 `- name:`）。"""
    m = re.search(
        rf"^\s*- name:\s*{re.escape(name)}\s*$(.*?)(?=^\s*- name:|\Z)",
        text, re.M | re.S,
    )
    return m.group(0) if m else None


print("=" * 72)
print("① keepalive.py 的 WORKFLOW ↔ 实际工作流文件")
print("=" * 72)
kl = (SCRIPTS / "keepalive.py").read_text(encoding="utf-8")
m = re.search(r'^WORKFLOW\s*=\s*["\']([^"\']+)["\']', kl, re.M)
if not m:
    fail("keepalive.py 里找不到 WORKFLOW 常量")
else:
    name = m.group(1)
    target = WF_DIR / name
    if target.exists():
        good(f"WORKFLOW = {name} → 文件存在")
    else:
        fail(f"WORKFLOW = {name} → 文件**不存在**！"
             f"（该文件已改名/删除，月度看护会静默失效）")
        print(f"      现有工作流: "
              f"{sorted(p.name for p in WF_DIR.glob('*.yml'))}")

print()
print("=" * 72)
print("② 工作流里引用的 scripts/*.py 是否都存在")
print("=" * 72)
for wf in sorted(WF_DIR.glob("*.yml")):
    text = wf.read_text(encoding="utf-8")
    refs = set(re.findall(r"scripts/([\w./-]+\.py)", text))
    for ref in sorted(refs):
        if (SCRIPTS / ref).exists():
            good(f"{wf.name:22s} scripts/{ref}")
        else:
            fail(f"{wf.name:22s} 引用 scripts/{ref} —— **文件不存在**")

print()
print("=" * 72)
print("③ 工作流里的 secrets 名称是否在白名单")
print("=" * 72)
for wf in sorted(WF_DIR.glob("*.yml")):
    text = wf.read_text(encoding="utf-8")
    names = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", text))
    for n in sorted(names):
        if n in ALLOWED_SECRETS:
            good(f"{wf.name:22s} secrets.{n}")
        else:
            fail(f"{wf.name:22s} secrets.{n} —— 未登记（拼错会导致 token 装不上）")
            print(f"      登记位置: scripts/selftest.py → ALLOWED_SECRETS")

print()
print("=" * 72)
print("④ ⭐ 提交数据的步骤：push 前必须**真正执行** pull --rebase")
print("=" * 72)
# 背景：采集耗时约 20 分钟，期间远程 main 可能前进（人/机器人推送）。
#      直接 push 会因非 fast-forward 被拒 ⇒ **整轮采集白跑**，
#      且失败点在最末尾（很难一眼看出原因）。
#      2026-09-30 run #13 就是这样失败的。
#
# ⚠️ 检查方式很讲究：必须校验"有一个**真命令**在跑 pull --rebase"，
#    而不是"文本里出现过这个词"。
#    第一版就写成了简单的 `"pull --rebase" in block` ——
#    结果连 `echo "── 尝试 1：pull --rebase 后推送 ──"` 都能骗过它
#    （反向验证时才发现：删掉真命令、只留 echo，检查居然还通过）。
#    ⇒ 现在的做法：先剥掉注释行，再用"命令位"正则匹配。
PULL_CMD_RE = re.compile(
    r"^\s*(?:if\s+|&&\s*|;\s*|\|\|\s*|then\s+|else\s+)*!?\s*"
    r"git\s+pull\s+[^\n]*--rebase",
    re.M,
)


def strip_comments(text: str) -> str:
    """去掉整行注释（`#` 开头的行）—— 注释里提到 pull --rebase 不算数。"""
    return "\n".join(
        ln for ln in text.splitlines() if not ln.strip().startswith("#")
    )


FOUND_ANY = False
for wf in sorted(WF_DIR.glob("*.yml")):
    text = wf.read_text(encoding="utf-8")
    if "git push" not in text:
        continue
    FOUND_ANY = True
    for name in re.findall(r"^\s*- name:\s*(.+?)\s*$", text, re.M):
        block = step_block(text, name.strip())
        if not block or "git push" not in block:
            continue
        if PULL_CMD_RE.search(strip_comments(block)):
            good(f"{wf.name:22s} 「{name.strip()}」确有 pull --rebase 命令")
        else:
            fail(f"{wf.name:22s} 「{name.strip()}」直接 push，"
                 f"未先执行 pull --rebase（并发推送会致整体失败）")
            print("      → 修法：git pull --rebase --autostash origin "
                  "$BRANCH && git push origin HEAD:$BRANCH")
            print("        并加重试循环（并发是瞬时的）")
if not FOUND_ANY:
    print("   ℹ️ 没有工作流执行 git push（跳过）")

print()
print("=" * 72)
print("⑤ ⭐ 自测步骤必须排在采集之前")
print("=" * 72)
# 理由：规则/契约/服务层测试若失败，说明代码被改坏了 ——
#      此时**不该**去写数据，否则坏规则会把脏数据写进库。
for wf in sorted(WF_DIR.glob("*.yml")):
    text = wf.read_text(encoding="utf-8")
    names = [n.strip() for n in re.findall(r"^\s*- name:\s*(.+?)\s*$", text, re.M)]
    idx_selftest = next((i for i, n in enumerate(names)
                         if "Self-test" in n), None)
    if idx_selftest is None:
        continue
    idx_harvest = next((i for i, n in enumerate(names)
                        if n.startswith("Harvest") or "采集" in n), None)
    if idx_harvest is None:
        good(f"{wf.name:22s} 有 Self-test，无采集步骤（顺序无冲突）")
    elif idx_selftest < idx_harvest:
        good(f"{wf.name:22s} Self-test（#{idx_selftest}）在采集"
             f"（#{idx_harvest}）之前")
    else:
        fail(f"{wf.name:22s} Self-test 排在采集**之后** —— "
             f"坏规则会先把脏数据写进库")

print()
print("=" * 72)
print("⑥ ⭐ README 里的相对链接是否指向真实文件")
print("=" * 72)
# 背景：README.md 由 scripts/export.py **自动生成**。
#      若生成器里写了一个链接（如 SERVICE.md），而该文件被改名/删除，
#      或生成器的改动**没有被提交**（2026-09-30 实际发生过：
#      export.py 的改动漏提交 → CI 用旧版生成器重写 README → 链接丢失），
#      读者就会点到一个 404。这类问题不报错，只能靠检查。
readme = ROOT / "README.md"
if not readme.is_file():
    fail("README.md 不存在")
else:
    text = readme.read_text(encoding="utf-8")
    # markdown 链接：](target)
    links = set(re.findall(r"\]\(([^)\s]+)\)", text))
    # ⚠️ 只查"**仓库内**的相对路径"：
    #    · 跳过 http(s)/mailto/锚点/绝对路径
    #    · 跳过以 `../` 开头的 —— 那是 GitHub **网页**相对链接
    #      （如 `../../issues` → GitHub 仓库的 Issues 页），不是本地文件。
    #      第一版没排除，导致 `../../issues` 被误报成"文件不存在"。
    #      ⇒ 教训：检查项也会误报，必须用真实仓库调准，别一写完就信。
    rel = sorted(
        l for l in links
        if not l.startswith(("http://", "https://", "mailto:", "#", "/", "../"))
    )
    if not rel:
        print("   ℹ️ README 里没有仓库内相对链接（跳过）")
    for l in rel:
        target = l.split("#")[0]
        if not target:
            continue
        if (ROOT / target).exists():
            good(f"README → {target}")
        else:
            fail(f"README 链接到 {target} —— **文件不存在**（读者会看到 404）")

print()
print("=" * 72)
print("总判定:", "✅ 全部通过" if ok else "❌ 有失败")
print("=" * 72)
sys.exit(0 if ok else 1)
