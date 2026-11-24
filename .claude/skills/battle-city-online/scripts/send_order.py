#!/usr/bin/env python3
"""Một lệnh gấp cho một xe (POST /orders). Lệnh nối cuối kế hoạch — dùng --stop-first để thay việc ngay.

  send_order.py ABC 2 fire up 3 --stop-first
  send_order.py ABC 1 move left 4
"""
import argparse

from _common import Client, dump, resolve_code
from bco_lib.client import ApiError

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("code")
ap.add_argument("tank", type=int)
ap.add_argument("do", choices=["move", "move_fire", "turn", "fire", "stop"])
ap.add_argument("direction", nargs="?", choices=["up", "right", "down", "left"])
ap.add_argument("n", nargs="?", type=int)
ap.add_argument("--stop-first", action="store_true")
a = ap.parse_args()

c = Client()
code = resolve_code(c, a.code if a.code != "-" else None)
step = {"tank": a.tank, "do": a.do}
if a.direction:
    step["direction"] = a.direction
if a.n is not None:
    step["count" if a.do == "fire" else "cells"] = a.n
try:
    if a.stop_first:
        c.stop(code, a.tank)
    dump(c.order(code, step))
except ApiError as e:
    print(f"HTTP {e.status}")
    dump(e.body)
