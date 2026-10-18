"""สร้างส่วนที่สร้างอัตโนมัติของ docs/DATABASE.md (ER diagram และพจนานุกรมข้อมูล) จาก wxdb/schema.py

  python scripts/build_db_docs.py
แทนที่ข้อความระหว่าง <!-- ER_START --> … <!-- ER_END --> และ <!-- DICT_START --> … <!-- DICT_END -->
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wxdb import catalog, schema  # noqa: E402

TYPE = {"string": "ข้อความ", "timestamp[s]": "เวลา (UTC)", "float": "ทศนิยม 32 บิต", "double": "ทศนิยม 64 บิต",
        "int8": "จำนวนเต็ม", "int16": "จำนวนเต็ม", "int32": "จำนวนเต็ม", "bool": "จริง/เท็จ", "date32[day]": "วันที่"}


def er_block():
    return "```mermaid\n" + schema.mermaid_er() + "\n```"


def dict_block():
    out = []
    for kind, title in (("dim", "ตารางมิติ (release `db-catalog`)"), ("fact", "ตารางข้อเท็จจริง (release `db-YYYY-MM`)")):
        out.append(f"### {title}\n")
        for name, t in schema.TABLES.items():
            if t["kind"] != kind:
                continue
            out.append(f"#### `{name}`\n\n{t['desc']}  \nคีย์หลัก: `{', '.join(t['pk'])}`"
                       + (f" · คีย์นอก: " + ", ".join(f"`{c}` → `{r}`" for c, r in t["fk"].items()) if t["fk"] else "") + "\n")
            out.append("| คอลัมน์ | ชนิด | คำอธิบาย |\n|---|---|---|")
            for c, typ, d in t["cols"]:
                out.append(f"| `{c}` | {TYPE.get(str(typ), str(typ))} | {d} |")
            out.append("")
    out.append("### รหัสตัวแปร (`variable`)\n")
    out.append("| var_id | ชื่อ | หน่วย | วิธีสรุปค่า |\n|---|---|---|---|")
    for v in catalog.VARIABLES:
        out.append(f"| `{v[0]}` | {v[1]} | {v[3]} | {v[4]} |")
    out.append("\n### แหล่งข้อมูล (`source`)\n")
    out.append("| source_id | แหล่งข้อมูล | หน่วยงาน | ประเภท | สัญญาอนุญาต |\n|---|---|---|---|---|")
    for s in catalog.SOURCES:
        name = f"[{s[1]}]({s[4]})" if s[4] else s[1]
        out.append(f"| `{s[0]}` | {name} | {s[2]} | {s[3]} | {s[5]} |")
    return "\n".join(out)


def replace(text, tag, body):
    return re.sub(rf"<!-- {tag}_START -->.*?<!-- {tag}_END -->", f"<!-- {tag}_START -->\n{body}\n<!-- {tag}_END -->", text, flags=re.S)


def main():
    p = ROOT / "docs" / "DATABASE.md"
    t = p.read_text(encoding="utf-8")
    t = replace(t, "ER", er_block())
    t = replace(t, "DICT", dict_block())
    p.write_text(t, encoding="utf-8")
    r = ROOT / "README.md"
    r.write_text(replace(r.read_text(encoding="utf-8"), "ER", "```mermaid\n" + schema.mermaid_er(keys_only=True) + "\n```"), encoding="utf-8")
    print("ปรับปรุง", p.relative_to(ROOT), "และ README.md")


if __name__ == "__main__":
    main()
