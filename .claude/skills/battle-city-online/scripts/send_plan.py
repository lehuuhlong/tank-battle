#!/usr/bin/env python3
"""Gửi kế hoạch nhiều xe một lần (POST /plan). Cú pháp gọn: TANK:DO[:DIR[:N]]

  send_plan.py ABC 1:move:up:4 1:fire:up:3 2:turn:left 3:move_fire:right:6
  send_plan.py ABC --stop-first 1:move:left:2      # stop xe 1 trước (đổi việc)
  send_plan.py ABC --json '{"steps":[{"tank":1,"do":"fire","direction":"up","count":3}]}'
N là cells với move/move_fire, count với fire. Xe bị từ chối được in ra để gửi lại riêng.
"""
import argparse
import json

from _common import Client, dump, resolve_code
from bco_lib.client import ApiError, find_rejections


def parse_step(tok):
    parts = tok.split(":")
    step = {"tank": int(parts[0]) if parts[0].isdigit() else parts[0], "do": parts[1]}
    if len(parts) > 2:
        step["direction"] = parts[2]
    if len(parts) > 3:
        step["count" if parts[1] == "fire" else "cells"] = int(parts[3])
    return step


ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("code")
ap.add_argument("steps", nargs="*")
ap.add_argument("--json")
ap.add_argument("--stop-first", action="store_true")
a = ap.parse_args()

c = Client()
code = resolve_code(c, a.code if a.code != "-" else None)
steps = json.loads(a.json)["steps"] if a.json else [parse_step(t) for t in a.steps]
if not steps:
    raise SystemExit("Chưa có bước nào.")
if a.stop_first:
    for tid in sorted({s["tank"] for s in steps}, key=str):
        c.stop(code, tid)
try:
    resp = c.plan(code, steps)
except ApiError as e:
    resp = e.body
    print(f"HTTP {e.status}")
dump(resp)
rej = find_rejections(resp, [s["tank"] for s in steps])
if rej:
    print("\n# XE BỊ TỪ CHỐI (sửa rồi gửi lại riêng):")
    for t, r in rej.items():
        print(f"#   T{t}: {r}")
