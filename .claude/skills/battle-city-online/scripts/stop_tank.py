#!/usr/bin/env python3
"""Xoá kế hoạch của xe (POST /stop). stop_tank.py ABC 1 3   |   stop_tank.py ABC all"""
import argparse

from _common import Client, dump, resolve_code

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("code")
ap.add_argument("tanks", nargs="+")
a = ap.parse_args()

c = Client()
code = resolve_code(c, a.code if a.code != "-" else None)
ids = [1, 2, 3, 4] if a.tanks == ["all"] else [int(t) for t in a.tanks]
for t in ids:
    dump({t: c.stop(code, t)})
