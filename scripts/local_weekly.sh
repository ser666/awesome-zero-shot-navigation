#!/usr/bin/env bash
# 本地周更：不依赖 GitHub Actions（账号 billing 锁定时的兜底方案）
#
# 用法：bash scripts/local_weekly.sh
# 建议：交给 cron（每周一 10:17 北京时间）
#
# ⚠️ 为什么要有这个脚本：
#   GitHub Actions 的 job 会因账号 billing 问题被锁（runner 根本不分配，
#   steps 为空、2 秒失败）。此时仓库内的一切自动化都失效，
#   但只要本地能跑 + git push 正常，网站依然可以更新（Pages 仍由 GitHub 托管）。
#
# 幂等性：本轮已入库的论文下一轮会被去重丢弃，所以与 Actions 同时存在也无害。

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY
export GH_PAT="${GH_PAT:-$(cat "$HOME/.hermes/secrets/github_pat" 2>/dev/null || true)}"

# ⚠️ 关键：remote 是 SSH（git@github.com:...），而本机没有默认私钥 ~/.ssh/id_*，
#    只有 ~/.ssh/github。所有走网络的 git 命令都必须带 GIT_SSH_COMMAND，
#    否则会报 "Permission denied (publickey)"。
#    （2026-11-09 / 11-16 两次周更就是因为 pull 没带这个而静默失败）
if [ -f "$HOME/.ssh/github" ]; then
  export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/github -o IdentitiesOnly=yes -o StrictHostKeyChecking=no"
fi

LOG_DIR="${HOME}/.hermes/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/awesome-weekly-$(date +%Y%m%d-%H%M).log"
exec > >(tee -a "$LOG") 2>&1

echo "═══ 本地周更开始 $(date '+%Y-%m-%d %H:%M:%S %Z') ═══"

# ⓿ 自愈：先把上一轮漏推的 commit 推上去（历史上失败过就必须补，不能等下一轮）
echo "── 补推遗留 commit ──"
if git fetch origin main --quiet 2>/dev/null; then
  AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo 0)
  if [ "${AHEAD:-0}" -gt 0 ]; then
    echo "⚠️ 发现 ${AHEAD} 个未推送的 commit，先补推"
    git pull --rebase --autostash origin main && git push origin HEAD:main \
      && echo "✅ 遗留 commit 已补推" \
      || echo "⚠️ 补推失败，继续本轮（末尾还会再推一次）"
  else
    echo "ℹ️ 无遗留 commit"
  fi
else
  echo "⚠️ 无法连接 origin（网络/认证），本轮照常跑，最后再试推送"
fi

# ① 门禁：自测先跑（坏规则不该去写数据）
echo "── 自测 ──"
python3 scripts/relevance.py   >/dev/null || { echo "❌ relevance 自测失败，放弃本轮"; exit 1; }
python3 scripts/topics.py      >/dev/null || { echo "❌ topics 自测失败，放弃本轮"; exit 1; }
python3 scripts/venues.py      >/dev/null || { echo "❌ venues 自测失败，放弃本轮"; exit 1; }
python3 scripts/links.py       >/dev/null || { echo "❌ links 自测失败，放弃本轮"; exit 1; }
python3 scripts/sources.py --selftest >/dev/null || { echo "❌ sources 契约失败，放弃本轮"; exit 1; }
python3 scripts/selftest.py    >/dev/null || { echo "❌ 仓库一致性失败，放弃本轮"; exit 1; }
python3 -m unittest discover -s tests -t . -q 2>&1 | tail -2
python3 -m unittest discover -s tests -t . -q >/dev/null 2>&1 || { echo "❌ 单测失败，放弃本轮"; exit 1; }
echo "✅ 自测全绿"

# ② 采集（失败不阻塞导出，保留旧数据可用）
echo "── 采集 ──"
python3 scripts/pipeline.py --days 21 --budget 600 || echo "⚠️ 采集异常（继续）"
python3 scripts/pipeline.py --refilter  || echo "⚠️ 重筛异常（继续）"
python3 scripts/pipeline.py --backfill  || echo "⚠️ 回填异常（继续）"
python3 scripts/pipeline.py --enrich --budget 900 || echo "⚠️ 增强异常（继续）"

# ③ 导出 + 一致性校验
echo "── 导出 ──"
python3 scripts/export.py || { echo "❌ 导出失败"; exit 1; }
python3 - <<'PY'
import json, pathlib, sqlite3, sys
db, js = pathlib.Path("data/papers.db"), pathlib.Path("docs/data/papers.json")
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
n_db = con.execute("SELECT COUNT(*) FROM papers").fetchone()[0]; con.close()
d = json.loads(js.read_text())
if d["total"] != n_db:
    sys.exit(f"❌ 数量不一致 DB={n_db} JSON={d['total']}")
if d["total"] < 100:
    sys.exit(f"❌ 论文数异常偏低 {d['total']}")
kb = js.stat().st_size // 1024
if kb > 900:
    sys.exit(f"❌ papers.json 过大 {kb}KB")
print(f"✅ 一致性 OK  DB={n_db} JSON={d['total']} payload={kb}KB generated_at={d['generated_at']}")
PY
[ $? -ne 0 ] && { echo "❌ 校验未通过，不提交"; exit 1; }

# ④ 提交（rebase + 重试，避免 20 分钟白跑）
echo "── 提交 ──"
git config user.name  "ser666"
git config user.email "1035534180@qq.com"
git add README.md docs/data/papers.json docs/data/abstracts.json data/papers.db

if git diff --staged --quiet; then
  echo "ℹ️ 无变化，跳过提交"
else
  git commit -m "chore(data): weekly update $(date -u +'%Y-%m-%d %H:%M UTC') [local]"
  ok=0
  for attempt in 1 2 3; do
    echo "── 尝试 ${attempt} ──"
    if ! git pull --rebase --autostash origin main; then
      # ⚠️ 注意：这里可能是【认证失败】也可能是【rebase 冲突】，要分清，别一律当冲突
      if git status --porcelain 2>/dev/null | grep -q '^UU\|^AA\|^DD'; then
        echo "⚠️ rebase 冲突（papers.db 是二进制，放弃本轮）"
        git rebase --abort 2>/dev/null || true
      else
        echo "❌ pull 失败（认证或网络问题，不是冲突）—— 检查 ~/.ssh/github 与 GIT_SSH_COMMAND"
      fi
    elif git push origin HEAD:main; then
      echo "✅ 已推送（第 ${attempt} 次）"; ok=1; break
    else
      echo "⚠️ push 失败，重试中…"
    fi
    sleep 5
  done
  [ "$ok" -ne 1 ] && { echo "❌ 推送失败（commit 已在本地，下轮开头会自动补推）"; exit 1; }
fi

echo "═══ 本地周更结束 $(date '+%Y-%m-%d %H:%M:%S %Z') ═══"
