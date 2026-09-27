#!/usr/bin/env bash
# commit ทุกอย่างใน archive/ แล้ว push ขึ้น branch data (ลองใหม่ถ้ามีรอบอื่น push ก่อน)
set -uo pipefail
cd archive
git add -A
if git diff --cached --quiet; then echo "ไม่มีข้อมูลใหม่"; exit 0; fi
git commit -qm "${1:-data}: $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  git push -q origin HEAD:data && exit 0
  git pull -q --rebase origin data
done
exit 1
