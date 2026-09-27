"""ดึงพยากรณ์อัตโนมัติ + บันทึกฝนที่ตกจริง + ดูคะแนนความแม่นยำ จาก command line

  python3 collector.py run                         # ดึงทุกตำแหน่งใน config.json 1 รอบ
  python3 collector.py run --export-dir archive --no-db   # (GitHub Actions) เขียนเป็นไฟล์ .csv.gz
  python3 collector.py sync                        # ดึงข้อมูลที่ GitHub Actions เก็บไว้ลงฐานข้อมูลในเครื่อง
  python3 collector.py location --action add --name "ที่ทำงาน" --coords "13.72, 100.53"
  python3 collector.py loop --every 60             # ดึงซ้ำทุก 60 นาที (Ctrl+C เพื่อหยุด)
  python3 collector.py observe --place me --rain yes --mm 5
  python3 collector.py observe --place "เชียงใหม่" --rain no --time "2026-09-27 15:30"
  python3 collector.py verify                      # ตารางคะแนนความแม่นยำ
  python3 collector.py install-launchd --every 60  # สร้างไฟล์ launchd ให้รันเองบน macOS
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

import weather_core as core
import weather_store as store


def cmd_run(args):
    log = store.collect_all(export_dir=getattr(args, "export_dir", None), to_db=not getattr(args, "no_db", False))
    print(pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(log.to_string(index=False))
    return 0 if (log.status == "ok").all() else 1


def cmd_loop(args):
    every = args.every or core.CFG["collect_every_minutes"]
    print(f"ดึงข้อมูลทุก {every} นาที (Ctrl+C เพื่อหยุด)")
    while True:
        try:
            cmd_run(args)
        except Exception as e:
            print(f"⚠️ {e}")
        time.sleep(every * 60)


def cmd_observe(args):
    if args.lat is not None and args.lon is not None:
        lat, lon, name = args.lat, args.lon, args.place or f"{args.lat:.3f},{args.lon:.3f}"
    elif args.place in (None, "me", "auto"):
        lat, lon, name = core.resolve(core.CFG["my_location"])
    else:
        lat, lon, name = core.geocode(args.place)
    obs_id = store.add_observation(name, lat, lon, rained=args.rain == "yes", amount_mm=args.mm,
                                   obs_time=args.time, note=args.note or "")
    print(f"บันทึกแล้ว #{obs_id}: {name} ({lat:.3f}, {lon:.3f}) ฝน{'ตก' if args.rain == 'yes' else 'ไม่ตก'}")


def _git(*a, check=True):
    return subprocess.run(["git", "-C", str(core.ROOT), *a], capture_output=True, text=True, check=check)


def cmd_sync(args):
    """ดึง branch data จาก GitHub มาไว้ที่ archive/ (git worktree) แล้วนำเข้า SQLite"""
    archive = core.ROOT / "archive"
    r = _git("fetch", "-q", "origin", "+refs/heads/data:refs/remotes/origin/data", check=False)
    if r.returncode != 0:
        print("ดึง branch data ไม่ได้ (ยังไม่มี remote หรือ GitHub Actions ยังไม่เคยรัน)\n" + r.stderr.strip())
        return 1
    if not (archive / ".git").exists():
        _git("worktree", "prune", check=False)
        _git("worktree", "add", "-q", "--detach", str(archive), "origin/data")
    else:
        subprocess.run(["git", "-C", str(archive), "checkout", "-q", "--detach", "origin/data"], check=True)
    new, total = store.import_archive(archive)
    print(f"นำเข้า {new} รอบใหม่ (ทั้งหมด {total} ไฟล์ใน branch data)")


def cmd_location(args):
    """แก้ตำแหน่งใน config.json (ใช้จาก workflow 'ตั้งค่าตำแหน่ง' บน GitHub หรือ command line)"""
    cfg = core.load_config()
    me, targets = cfg["my_location"], list(cfg["target_places"])
    label = lambda x: x if isinstance(x, str) else x[2]
    name = (args.name or "").strip()

    if args.action == "remove":
        before = len(targets)
        targets = [t for t in targets if label(t) != name]
        if len(targets) == before:
            sys.exit(f"ไม่พบตำแหน่งชื่อ '{name}' ใน target_places: {[label(t) for t in targets]}")
    else:
        xy = core.parse_coords(args.coords)
        if xy:
            loc = [round(xy[0], 5), round(xy[1], 5), name or f"{xy[0]:.3f},{xy[1]:.3f}"]
        elif args.coords and args.coords.strip():
            la, lo, found = core.geocode(args.coords.strip())      # ใส่ชื่อสถานที่แทนพิกัดก็ได้
            loc = [round(la, 5), round(lo, 5), name or found]
        else:
            sys.exit("กรุณาใส่พิกัด เช่น '13.7563, 100.5018' ลิงก์ Google Maps หรือชื่อสถานที่")
        if args.action == "me":
            me = loc
        else:
            targets = [t for t in targets if label(t) != loc[2]] + [loc]
        if not core.in_tmd_domain(loc[0], loc[1]):
            print("⚠️ ตำแหน่งนี้อยู่นอกพื้นที่ TMD WRF จะมีเฉพาะโมเดลระดับโลก")
    core.save_locations(me, targets)
    print(f"ตำแหน่งของฉัน: {me}\nตำแหน่งที่ต้องการ: {targets}")


def cmd_verify(args):
    rec = store.verification_records(use_previous_runs=not args.no_previous_runs)
    if rec.empty:
        print("ยังไม่มีข้อมูลให้เปรียบเทียบ — บันทึกฝนที่ตกจริงด้วยคำสั่ง observe ก่อน")
        return
    det, ens = store.verification_scores(rec, by_lead=False)
    pd.set_option("display.width", 200)
    print(f"จำนวนการสังเกต: {rec.obs_id.nunique()}")
    print("\n== โมเดล deterministic (เรียงตาม CSI) ==")
    print(det.round(2).to_string())
    if not ens.empty:
        print("\n== Ensemble (Brier ยิ่งต่ำยิ่งดี) ==")
        print(ens.round(3).to_string())


PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python}</string>
    <string>{script}</string>
    <string>run</string>
  </array>
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartInterval</key><integer>{seconds}</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{root}/logs/collector.log</string>
  <key>StandardErrorPath</key><string>{root}/logs/collector.log</string>
</dict>
</plist>
"""


def cmd_install_launchd(args):
    every = args.every or core.CFG["collect_every_minutes"]
    label = "local.consolidated-weather.collector"
    root = core.ROOT
    (root / "logs").mkdir(exist_ok=True)
    out = root / "scripts" / f"{label}.plist"
    out.parent.mkdir(exist_ok=True)
    out.write_text(PLIST.format(label=label, python=sys.executable, script=root / "collector.py",
                                root=root, seconds=every * 60), encoding="utf-8")
    dest = Path.home() / "Library" / "LaunchAgents" / out.name
    print(f"สร้างไฟล์แล้ว: {out}\n\nติดตั้งให้รันทุก {every} นาที:\n"
          f"  cp '{out}' '{dest}'\n  launchctl load '{dest}'\n\n"
          f"หยุด:\n  launchctl unload '{dest}'")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("--export-dir", help="เขียนแต่ละรอบเป็น .csv.gz ในโฟลเดอร์นี้")
    s.add_argument("--no-db", action="store_true", help="ไม่บันทึกลง SQLite")
    s.set_defaults(func=cmd_run)
    sub.add_parser("sync").set_defaults(func=cmd_sync)
    s = sub.add_parser("location")
    s.add_argument("--action", choices=["me", "add", "remove"], required=True)
    s.add_argument("--name")
    s.add_argument("--coords", help="'lat, lon' / ลิงก์ Google Maps / ชื่อสถานที่")
    s.set_defaults(func=cmd_location)
    s = sub.add_parser("loop")
    s.add_argument("--every", type=int, help="นาที")
    s.set_defaults(func=cmd_loop)
    s = sub.add_parser("observe")
    s.add_argument("--place", help="me | ชื่อสถานที่")
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--rain", choices=["yes", "no"], required=True)
    s.add_argument("--mm", type=float, help="ปริมาณฝน (ถ้ามี)")
    s.add_argument("--time", help="เวลาท้องถิ่น เช่น '2026-09-27 15:30' (ไม่ใส่ = ตอนนี้)")
    s.add_argument("--note")
    s.set_defaults(func=cmd_observe)
    s = sub.add_parser("verify")
    s.add_argument("--no-previous-runs", action="store_true")
    s.set_defaults(func=cmd_verify)
    s = sub.add_parser("install-launchd")
    s.add_argument("--every", type=int, help="นาที")
    s.set_defaults(func=cmd_install_launchd)
    args = p.parse_args()
    sys.exit(args.func(args) or 0)


if __name__ == "__main__":
    main()
