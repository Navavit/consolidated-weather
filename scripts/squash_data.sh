#!/usr/bin/env bash
# ยุบประวัติของ branch data เหลือ commit เดียว (เนื้อหาไฟล์ปัจจุบันคงเดิม) แล้ว force-push
# ข้อมูลเก่าทั้งหมดอยู่ในฐานข้อมูล wxdb (GitHub Releases) แล้ว — ใช้หลัง `python -m wxdb prune`
# ต้องรันภายใต้ concurrency group "data-branch" (job monthly ใน database.yml) เพื่อไม่ให้ชนกับรอบเก็บข้อมูลรายชั่วโมง
# GitHub ลบ object ที่ไม่มีการอ้างอิงออกจากขนาด repository ภายหลัง (garbage collection ฝั่งเซิร์ฟเวอร์)
set -euo pipefail
cd archive
before=$(git rev-list --count HEAD 2>/dev/null || echo "?")
git checkout -q --orphan squashed
git add -A
git commit -qm "data: ยุบประวัติ $(date -u +%Y-%m-%d) — ข้อมูลก่อนหน้าอยู่ในฐานข้อมูล wxdb (GitHub Releases)"
git push -q --force origin squashed:data
echo "branch data: ${before} commit (ในสำเนาตื้น) → 1 commit · ขนาดไฟล์ $(du -sh --exclude=.git . 2>/dev/null | cut -f1)"
