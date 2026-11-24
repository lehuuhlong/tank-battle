#!/usr/bin/env python3
"""Đọc luật của trận (một lần) và lưu bco-data/<code>/rules.json."""
import argparse
import os

from _common import Client, dump, match_dir, resolve_code, save_json

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("code", nargs="?")
a = ap.parse_args()
c = Client()
code = resolve_code(c, a.code)
rules = c.rules(code)
p = save_json(os.path.join(match_dir(code), "rules.json"), rules)
dump(rules)
print(f"\n# đã lưu {p}")
