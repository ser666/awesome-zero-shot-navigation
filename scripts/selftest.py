#!/usr/bin/env python3
"""仓库级一致性自测 — 防"静默失效"类 bug

目前检查：
  ① keepalive.py 的 WORKFLOW 常量，必须对应一个**真实存在**的工作流文件
     （曾经残留已删除的 daily-update.yml → 月度看护静默失效）
  ② 工作流 YAML 里引用的 scripts/*.py，必须都真实存在
     （改了文件名忘了改 workflow → 云端跑起来才报错）
  ③ 工作流里引用 secrets 的地方，只允许白名单内的名字
     （防止写错 secret 名导致 token 装载失败且不报错）

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
print("总判定:", "✅ 全部通过" if ok else "❌ 有失败")
print("=" * 72)
sys.exit(0 if ok else 1)
