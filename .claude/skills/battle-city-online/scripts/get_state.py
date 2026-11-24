#!/usr/bin/env python3
"""Lấy state?map=true, lưu bco-data/<code>/state-<n>.json. --summary in bản tóm tắt đã chuẩn hoá."""
import argparse
import os
import time

from _common import Client, cached_side, dump, match_dir, resolve_code, save_json
from bco_lib.state import normalize

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("code", nargs="?")
ap.add_argument("--out", help="đường dẫn file lưu")
ap.add_argument("--no-map", action="store_true")
ap.add_argument("--summary", action="store_true")
ap.add_argument("--side", choices=["top", "bottom"])
a = ap.parse_args()

c = Client()
code = resolve_code(c, a.code)
st = c.state(code, with_map=not a.no_map)
out = a.out or os.path.join(match_dir(code), f"state-{int(time.time())}.json")
save_json(out, st)
save_json(os.path.join(match_dir(code), "state-latest.json"), st)
if a.summary:
    g = normalize(st, a.side or cached_side(code))
    print(f"status={g.status} time_left={g.time_left} armor={g.armor_active} score={g.my_score}-{g.enemy_score} base={g.my_base_hp}-{g.enemy_base_hp}")
    for t in g.my_tanks:
        print(f"  ta  T{t.id} ({t.x},{t.y}) {t.dir} lives={t.lives} stars={t.stars} busy={t.busy}")
    for t in g.enemy_tanks:
        print(f"  địch T{t.id} ({t.x},{t.y}) {t.dir} lives={t.lives}")
    for w in g.warnings:
        print("  !", w)
else:
    dump(st)
print(f"# đã lưu {out}")
