#!/usr/bin/env python3
"""Liệt kê trận. --playing: chỉ in mã các trận đang cần chơi (playing=true)."""
import argparse

from _common import Client, dump, match_code, match_list

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--playing", action="store_true")
ap.add_argument("--raw", action="store_true", help="in JSON gốc")
a = ap.parse_args()

resp = Client().matches()
if a.raw:
    dump(resp)
else:
    for m in match_list(resp):
        if a.playing and not m.get("playing"):
            continue
        if a.playing:
            print(match_code(m))
        else:
            extra = {k: v for k, v in m.items() if k in ("status", "playing", "opponent", "starts_at", "side")}
            print(match_code(m), extra)
