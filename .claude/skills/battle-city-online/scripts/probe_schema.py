#!/usr/bin/env python3
"""Dò cấu trúc state: in mọi đường dẫn trường + giá trị mẫu, những gì adapter tự nhận ra, và chỗ còn thiếu.

  probe_schema.py bco-data/ABC/state-latest.json --side bottom
  probe_schema.py state.json --side bottom --write   # ghi các đường dẫn tìm được vào state-schema.json
"""
import argparse
import json

from bco_lib import SCHEMA_PATH
from bco_lib.state import load_schema, normalize

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("file")
ap.add_argument("--side", choices=["top", "bottom"])
ap.add_argument("--write", action="store_true", help="ghi đường dẫn tự dò được vào state-schema.json (chỉ khoá đang null)")
ap.add_argument("--depth", type=int, default=6)
a = ap.parse_args()

state = json.load(open(a.file, encoding="utf-8"))


def walk(node, path, depth):
    if depth > a.depth:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f"{path}.{k}" if path else k, depth + 1)
    elif isinstance(node, list):
        print(f"{path:45s} list[{len(node)}]")
        if node and not all(isinstance(x, str) for x in node):
            walk(node[0], f"{path}.0", depth + 1)
    else:
        s = repr(node)
        print(f"{path:45s} {s[:60] + ('…' if len(s) > 60 else '')}")


print("== Các trường trong state ==")
walk(state, "", 0)

schema = load_schema()
try:
    g = normalize(state, a.side, schema)
except ValueError as e:
    raise SystemExit(f"\n!! {e}")
print("\n== Adapter tìm thấy ==")
for k, v in sorted(g.found.items()):
    print(f"  {k:14s} ← {v}")
missing = [k for k in ("status", "time_left", "armor_left", "my_tanks", "enemy_tanks", "my_base_hp", "enemy_base_hp",
                       "my_score", "enemy_score", "events", "items", "map") if k not in g.found]
print("\n== Chưa tìm thấy (điền tay trong references/state-schema.json) ==")
print("  " + (", ".join(missing) or "không có — tốt"))
print(f"\n== Kết quả chuẩn hoá ==\n  side={g.my_side} status={g.status} time_left={g.time_left} armor={g.armor_active} "
      f"score={g.my_score}-{g.enemy_score} base={g.my_base_hp}-{g.enemy_base_hp} map={g.grid.W}x{g.grid.H} items={len(g.items)}")
for t in g.my_tanks:
    print(f"  ta   T{t.id} ({t.x},{t.y}) dir={t.dir} lives={t.lives} stars={t.stars} order={t.order!r} busy={t.busy}")
for t in g.enemy_tanks:
    print(f"  địch T{t.id} ({t.x},{t.y}) dir={t.dir} lives={t.lives} shield={t.shield}")
for w in g.warnings:
    print("  !", w)
if g.my_tanks and g.tank_size == 2 and any(t.x % 1 or t.y % 1 for t in g.my_tanks):
    print("  ! toạ độ xe không nguyên — kiểm tra lại tank_size / đơn vị toạ độ")

if a.write:
    changed = []
    for k, p in g.found.items():
        if k in schema and schema[k] is None and "[" not in p:
            schema[k] = p
            changed.append(k)
    if g.my_side and not schema.get("my_side"):
        pass
    json.dump(schema, open(SCHEMA_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n# đã ghi {changed} vào {SCHEMA_PATH}")
