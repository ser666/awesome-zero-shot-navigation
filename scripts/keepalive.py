#!/usr/bin/env python3
"""防停看护 — 解决 GitHub「60 天无活动自动禁用定时任务」的问题

为什么需要它：
  GitHub 官方文档：In a public repository, scheduled workflows are
  automatically disabled when no repository activity has occurred in 60 days.
  ⚠️ 而且是【悄悄禁用】—— 不报错，你可能以为一直在跑。

做法（每 30 天由本机跑一次）：
  1. 检查仓库最近提交时间 / 数据新鲜度
  2. 若定时任务被禁用 → 用 API 自动重新启用
  3. 用 PAT 做一次真实 commit（归属本人账号 = 铁定算"仓库活动"）
  4. 若数据过期 → 触发一次 workflow_dispatch 补跑

用法：
    python3 keepalive.py            # 检查 + 必要时补救
    python3 keepalive.py --dry-run  # 只看状态，不改动
"""

from __future__ import annotations

import base64
import json
import pathlib
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

PAT = pathlib.Path.home().joinpath(".hermes/secrets/github_pat").read_text().strip()
OWNER, NAME = "ser666", "awesome-zero-shot-navigation"
REPO = f"{OWNER}/{NAME}"
HDR = {"Authorization": f"Bearer {PAT}",
       "Accept": "application/vnd.github+json",
       "User-Agent": "hermes-agent"}
# ⚠️ 必须是**当前真实存在**的工作流文件名。
# 这里曾残留已删除的 "daily-update.yml"（已改名 weekly-update.yml），
# 会导致月度看护查不到工作流、静默失效 —— 改动工作流文件名时务必同步这里。
WORKFLOW = "weekly-update.yml"

DRY = "--dry-run" in sys.argv


def call(method, path, payload=None, accept=None):
    h = dict(HDR)
    if accept:
        h["Accept"] = accept
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data,
                                headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode()
            return r.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


# ── 1. 仓库最近活动
print("=" * 72)
print("① 检查仓库活动")
print("=" * 72)
st, commits = call("GET", f"/repos/{REPO}/commits?per_page=1")
last_push = None
if st == 200 and commits:
    c = commits[0]
    last_push = c["commit"]["committer"]["date"]
    print(f"  最近提交: {last_push}  ({c['commit']['message'].splitlines()[0][:60]})")
    age = (datetime.now(timezone.utc)
           - datetime.fromisoformat(last_push.replace("Z", "+00:00"))).days
    print(f"  距今: {age} 天  （⚠️ 阈值 60 天）")
else:
    print(f"  ⚠️ 查询失败: {st} {commits}")

# ── 2. 定时任务是否被禁用
print()
print("=" * 72)
print("② 检查定时任务状态（是否被 GitHub 静默禁用）")
print("=" * 72)
st, wf = call("GET", f"/repos/{REPO}/actions/workflows/{WORKFLOW}")
disabled = None
if st == 200:
    disabled = wf.get("state")
    print(f"  状态: {disabled}  （active=正常 / disabled_inactivity=被静默禁用）")
    if disabled != "active" and not DRY:
        st2, r2 = call("PUT", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/enable")
        print(f"  → 重新启用: {st2} {'✅ 已恢复' if st2 in (200, 204) else r2}")
    elif disabled != "active":
        print("  → (dry-run) 应当重新启用")
else:
    print(f"  ⚠️ 查询失败: {st} {wf}")

# ── 3. 数据新鲜度
print()
print("=" * 72)
print("③ 检查数据新鲜度")
print("=" * 72)
st, f = call("GET", f"/repos/{REPO}/contents/docs/data/papers.json",
             accept="application/vnd.github.raw+json")
stale_days = None
# ⚠️ 关键：必须用 **raw 媒体类型**。
#    Contents API 对 **>1MB** 的文件**不返回内联 content**（content 字段是空串，
#    而是提示改用 media/raw 接口）。而 papers.json 已达 ~1.4MB ——
#    用默认 Accept 会拿到空 content，base64 解出空串、json 解析报
#    "Expecting value: line 1 column 1 (char 0)"，
#    结果是 stale_days 永远为 None，
#    **"数据过期 → 触发补跑"这条分支静默失效**（看护等于少了一半）。
#    raw 媒体类型直接返回文件原文，可支持到 100MB。
if st == 200 and isinstance(f, dict) and "generated_at" in f:
    try:
        gen = datetime.fromisoformat(f["generated_at"].replace("Z", "+00:00"))
        stale_days = (datetime.now(timezone.utc) - gen).days
        print(f"  数据生成于: {gen.date()}（{stale_days} 天前）")
        print(f"  论文总数:   {f.get('total')}")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️ 解析失败: {e}")
elif st == 200:
    got = list(f)[:6] if isinstance(f, dict) else type(f).__name__
    print(f"  ⚠️ 返回结构异常（期望含 generated_at，实际 keys={got}）")
else:
    print(f"  ⚠️ 读取失败: HTTP {st} {str(f)[:160]}")

if stale_days is not None and stale_days > 3 and not DRY:
    st2, r2 = call("POST", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches",
                   {"ref": "main"})
    print(f"  → 触发补跑: {st2} {'✅ 已触发' if st2 in (200, 204) else r2}")

# ── 4. 用 PAT 做一次真实 commit（铁定的"活动"）
print()
print("=" * 72)
print('④ 制造一次「用户账号活动」（防 60 天静默停用）')
print("=" * 72)
KEEP = "data/.keepalive"
st, info = call("GET", f"/repos/{REPO}/contents/{KEEP}")
sha = info.get("sha") if st == 200 else None
content = (f"keepalive by ser666 ({OWNER} account commit)\n"
           f"purpose: GitHub disables scheduled workflows after 60 days of no\n"
           f"          repository activity — this commit keeps the schedule alive.\n"
           f"updated: {datetime.now(timezone.utc).isoformat()}\n")

if DRY:
    print("  (dry-run) 跳过提交")
else:
    payload = {
        "message": f"chore: keepalive (scheduled-workflow activity) "
                   f"{datetime.now(timezone.utc).strftime('%Y-%m')}",
        "content": base64.b64encode(content.encode()).decode(),
        "committer": {"name": "ser666", "email": "1035534180@qq.com"},
        "author": {"name": "ser666", "email": "1035534180@qq.com"},
    }
    if sha:
        payload["sha"] = sha
    st, r = call("PUT", f"/repos/{REPO}/contents/{KEEP}", payload)
    if st in (200, 201):
        print(f"  ✅ 已提交（同一 commit 顺带刷新活动时间）")
    else:
        print(f"  ⚠️ 提交失败: {st} {r}")

print()
print("=" * 72)
print("结论")
print("=" * 72)
print(f"  定时任务状态: {disabled}")
print(f"  最近提交:     {last_push}")
print(f"  数据新鲜度:   {stale_days} 天前" if stale_days is not None else "  数据新鲜度:   n/a")
print("  ✅ 看护完成" if not DRY else "  （dry-run 模式，未做改动）")
