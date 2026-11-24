#!/usr/bin/env python3
"""Phân tích một state offline: vẽ bản đồ có xe, và cho xem MỖI chiến thuật sẽ ra lệnh gì (không gửi gì).

  analyze_state.py bco-data/ABC/state-latest.json --side bottom
  analyze_state.py state.json --side bottom --strategy siege --strategy hunter
"""
import argparse
import json

from bco_lib.grid import render
from bco_lib.state import normalize
from bco_lib.tactics import defense_posts
import strategies

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("file")
ap.add_argument("--side", choices=["top", "bottom"])
ap.add_argument("--strategy", action="append", help="lặp lại để so sánh; mặc định tất cả")
ap.add_argument("--param", action="append", default=[])
ap.add_argument("--no-map", action="store_true")
a = ap.parse_args()

state = json.load(open(a.file, encoding="utf-8"))
g = normalize(state, a.side)
params = dict(p.split("=", 1) for p in a.param)

if not a.no_map:
    ov = {}
    for t in g.my_tanks:
        ov[t.pos] = str(t.id)[-1]
    for t in g.enemy_tanks:
        ov[t.pos] = "abcdefgh"[(int(t.id) - 1) % 8] if str(t.id).isdigit() else "e"
    ov[g.my_base] = "B"
    ov[g.enemy_base] = "X"
    for it in g.items:
        ov[(it.x, it.y)] = "*"
    for p in defense_posts(g, {}):
        ov.setdefault(p, "+")
    print(render(g.grid, ov))
    print("chú thích: 1-4 xe ta (góc trên-trái) · a-d xe địch · B đại bàng ta · X đại bàng địch · * vật phẩm · + chốt thủ")

print(f"\nside={g.my_side} armor={g.armor_active} time_left={g.time_left} score={g.my_score}-{g.enemy_score} base={g.my_base_hp}-{g.enemy_base_hp}")
for w in g.warnings:
    print("!", w)

names = a.strategy or sorted(strategies.load_all())
for name in names:
    strat = strategies.get(name, **params)
    mem = {}
    decisions = strat.decide(g, mem)
    phase = f" (phase={mem['adaptive_phase']})" if mem.get("adaptive_phase") else ""
    print(f"\n== {name}{phase} ==")
    for tid, d in sorted(decisions.items(), key=lambda kv: str(kv[0])):
        steps = " ; ".join(f"{s['do']} {s.get('direction', '')} {s.get('cells', s.get('count', ''))}".strip() for s in d.steps)
        print(f"  T{tid}: {d.job:45s} {steps or '(giữ nguyên)'}{'  [GẤP]' if d.urgent else ''}")
