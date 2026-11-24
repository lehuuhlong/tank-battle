#!/usr/bin/env python3
"""Lấy thông tin trận (gửi X-Agent-Model), đoán bên top/bottom, lưu bco-data/<code>/match.json + side.txt."""
import argparse
import os

from _common import Client, dump, match_dir, resolve_code, save_json
from bco_lib.state import detect_side

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("code", nargs="?", help="mã trận (bỏ trống = trận playing)")
ap.add_argument("--model", help="giá trị X-Agent-Model (mặc định env BCO_AGENT_MODEL)")
a = ap.parse_args()

c = Client(model=a.model)
code = resolve_code(c, a.code)
info = c.match(code)
d = match_dir(code)
save_json(os.path.join(d, "match.json"), info)
side = detect_side(info)
if side:
    open(os.path.join(d, "side.txt"), "w").write(side)
dump(info)
print(f"\n# match={code} side={side or 'KHÔNG RÕ — xem JSON và truyền --side'}  (lưu ở {d})")
