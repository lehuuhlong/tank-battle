#!/usr/bin/env python3
"""Tìm bản đồ ASCII trong file state JSON (hoặc file .txt), in kích thước, thống kê ký tự, đường dẫn trường.

  extract_map.py bco-data/ABC/state-latest.json [--out map.txt]
"""
import argparse
import collections
import json

from bco_lib.grid import Grid
from bco_lib.state import find_map_rows, load_schema

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("file")
ap.add_argument("--out")
a = ap.parse_args()

raw = open(a.file, encoding="utf-8").read()
found = {}
try:
    rows = find_map_rows(json.loads(raw), load_schema(), found)
except json.JSONDecodeError:
    rows = [r for r in raw.splitlines() if r]
    found["map"] = "<plain text>"
if not rows:
    raise SystemExit("Không tìm thấy bản đồ (cần chuỗi nhiều dòng hoặc mảng chuỗi cùng độ dài). Gọi state?map=true chưa?")

schema = load_schema()
g = Grid(rows, schema.get("legend"), schema.get("unknown_char_as", "empty"))
print("\n".join(rows))
print(f"\n# trường: {found.get('map')}  kích thước: {g.W}x{g.H}")
counts = collections.Counter("".join(rows))
legend = schema.get("legend", {})
for ch, n in counts.most_common():
    print(f"#   '{ch}': {n:5d}  → {legend.get(ch, 'CHƯA CÓ TRONG LEGEND')}")
if g.unknown_chars:
    print(f"# !! thêm {sorted(g.unknown_chars)} vào legend trong references/state-schema.json")
if a.out:
    open(a.out, "w", encoding="utf-8").write("\n".join(rows) + "\n")
    print(f"# đã lưu {a.out}")
