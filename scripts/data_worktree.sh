#!/usr/bin/env bash
# เตรียม branch "data" ไว้ที่โฟลเดอร์ archive/ (ใช้ใน GitHub Actions) — สร้าง branch ใหม่ถ้ายังไม่มี
set -euo pipefail
git config --global user.name "github-actions[bot]"
git config --global user.email "41898282+github-actions[bot]@users.noreply.github.com"
if git ls-remote --exit-code --heads origin data > /dev/null; then
  git fetch --depth=1 origin data
  git worktree add --detach archive FETCH_HEAD
else
  git worktree add --detach archive HEAD
  (cd archive && git checkout -q --orphan data && git rm -rfq .)
fi
