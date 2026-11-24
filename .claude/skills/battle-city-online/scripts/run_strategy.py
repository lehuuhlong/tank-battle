#!/usr/bin/env python3
"""Vòng chơi tự động: state → chiến thuật → /stop + /plan, lặp mỗi --interval giây đến khi trận kết thúc.

  run_strategy.py [CODE] --strategy adaptive
  run_strategy.py ABC --strategy balanced --param roles=siege,siege,collect,defend
  run_strategy.py ABC --dry-run --once           # chỉ in quyết định, không gửi

Đổi chiến thuật khi đang chạy (không cần dừng): ghi bco-data/<code>/control.json
  {"strategy": "fortress", "params": {"hunters": "1"}}      hoặc {"pause": true}  /  {"quit": true}
Nhật ký từng nhịp: bco-data/<code>/log.jsonl
"""
import argparse
import json
import os
import sys
import time

from _common import Client, cached_side, match_dir, parse_params, resolve_code, save_json
from bco_lib.client import ApiError
from bco_lib.runner import Commander, summary_line
from bco_lib.state import detect_side, normalize
import strategies

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("code", nargs="?")
ap.add_argument("--strategy", default="adaptive")
ap.add_argument("--plan", help="file kế hoạch JSON → dùng chiến thuật scripted")
ap.add_argument("--separate-stop", action="store_true", help="gọi POST /stop riêng thay vì đặt stop đầu /plan")
ap.add_argument("--param", action="append", default=[])
ap.add_argument("--side", choices=["top", "bottom"])
ap.add_argument("--interval", type=float, default=0.8, help="giây giữa hai nhịp (nhịp lệnh server 0,5s)")
ap.add_argument("--refresh", type=float, default=4.0, help="gửi lại kế hoạch cùng việc sau N giây")
ap.add_argument("--dry-run", action="store_true")
ap.add_argument("--once", action="store_true")
ap.add_argument("--max-minutes", type=float, default=40, help="giới hạn an toàn (gồm cả thời gian chờ trận)")
ap.add_argument("--quiet", action="store_true")
a = ap.parse_args()

client = Client()
code = resolve_code(client, a.code)
mdir = match_dir(code)

# Always call GET /matches/{code} first (with X-Agent-Model): the room's "Ready" button only
# unlocks once the agent has called the API for this room.
info = None
try:
    info = client.match(code)
    save_json(os.path.join(mdir, "match.json"), info)
except ApiError as e:
    print("! GET /matches/{code} lỗi:", e, file=sys.stderr)
side = a.side or cached_side(code)
if not side and info is not None:
    side = detect_side(info)
    if side:
        open(os.path.join(mdir, "side.txt"), "w").write(side)
if not side:
    sys.exit("Không đoán được bên top/bottom từ GET /matches/{code}. Chạy lại với --side.")
if not os.path.exists(os.path.join(mdir, "rules.json")):
    try:
        save_json(os.path.join(mdir, "rules.json"), client.rules(code))
    except ApiError as e:
        print("! không đọc được rules:", e, file=sys.stderr)

control_path = os.path.join(mdir, "control.json")
strategy_name, params = a.strategy, parse_params(a.param)
if a.plan:
    strategy_name, params["plan"] = "scripted", os.path.abspath(a.plan)


def with_defaults(name, prm):
    if name == "scripted" and "plan" not in prm:
        prm = dict(prm, plan=os.path.join(mdir, "plan.json"))
    return prm


params = with_defaults(strategy_name, params)
cmd = Commander(client, code, strategies.get(strategy_name, **params), dry_run=a.dry_run,
                refresh_s=a.refresh, log_path=os.path.join(mdir, "log.jsonl"), inline_stop=not a.separate_stop)
print(f"# match={code} side={side} strategy={strategy_name} params={params} dry_run={a.dry_run}", flush=True)

control_mtime, paused, deadline, errors, waiting_logged = None, False, time.time() + a.max_minutes * 60, 0, False
while time.time() < deadline:
    t0 = time.time()
    # -- hot reload control file
    if os.path.exists(control_path) and os.path.getmtime(control_path) != control_mtime:
        control_mtime = os.path.getmtime(control_path)
        try:
            ctl = json.load(open(control_path, encoding="utf-8"))
            if ctl.get("quit"):
                print("# quit theo control.json")
                break
            paused = bool(ctl.get("pause"))
            if ctl.get("strategy") and (ctl["strategy"] != strategy_name or ctl.get("params", {}) != params):
                strategy_name = ctl["strategy"]
                params = with_defaults(strategy_name, {k: str(v) for k, v in ctl.get("params", {}).items()})
                cmd.strategy = strategies.get(strategy_name, **params)
                for tm in cmd.tanks.values():
                    tm["job"] = None
                print(f"# đổi chiến thuật → {strategy_name} {params}", flush=True)
        except (json.JSONDecodeError, SystemExit) as e:
            print("! control.json lỗi:", e, file=sys.stderr)
    try:
        raw = client.state(code)
        errors = 0
    except Exception as e:  # network hiccup: keep going
        errors += 1
        print("! state lỗi:", e, file=sys.stderr)
        if errors > 20:
            sys.exit("Quá nhiều lỗi liên tiếp.")
        time.sleep(1)
        continue
    save_json(os.path.join(mdir, "state-latest.json"), raw)
    game = normalize(raw, side)
    if game.finished:
        print(f"# TRẬN KẾT THÚC score {game.my_score}-{game.enemy_score} base {game.my_base_hp}-{game.enemy_base_hp}", flush=True)
        break
    if game.waiting:  # scheduled → live: keep polling (the loop must not exit before the match)
        if a.once:
            print(f"# trận chưa bắt đầu (status={game.status})")
            break
        if not waiting_logged:
            print(f"# chờ trận bắt đầu (status={game.status})... bấm Sẵn sàng trên trang phòng", flush=True)
            waiting_logged = True
        time.sleep(1)
        continue
    if paused:
        time.sleep(a.interval)
        continue
    report, rejected = cmd.step(game)
    if not a.quiet:
        print(summary_line(game, report, rejected, cmd.memory), flush=True)
    if a.once:
        for tid, r in report.items():
            print(f"  T{tid}: {r}")
        break
    time.sleep(max(0.05, a.interval - (time.time() - t0)))
