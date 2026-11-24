#!/usr/bin/env python3
"""Battle City Online — CLI cho agent. Dữ liệu trận lưu ở ./bco-data/<CODE>/ (đổi bằng BCO_DATA_DIR).

  bco.py matches [--playing]            danh sách trận
  bco.py info CODE                      thông tin trận + bên top/bottom (gọi sớm để bật nút Sẵn sàng)
  bco.py rules CODE                     luật trận (đọc một lần)
  bco.py state CODE [--summary]         lưu state?map=true
  bco.py probe FILE --side S [--write]  dò tên trường state + thống kê bản đồ
  bco.py analyze FILE --side S [--strategy N]... [--param k=v]   bản đồ + quyết định của chiến thuật
  bco.py strategies                     danh sách chiến thuật + vai
  bco.py run [CODE] [--strategy N | --plan F] ...                 vòng chơi tự động (chạy nền)
  bco.py watch CODE [--seconds 25]      chờ + tóm tắt; mã thoát 3 = trận kết thúc, 4 = runner chết
  bco.py plan CODE 1:stop 1:move:up:4 2:fire:left:3               lệnh tay nhiều xe (TANK:DO[:DIR[:N]])
  bco.py order CODE TANK DO [DIR] [N]   một lệnh gấp
  bco.py stop CODE 1 2 | all            xoá kế hoạch xe
"""
import argparse
import collections
import json
import os
import sys
import time

from bco_core import (SCHEMA_PATH, ApiError, Client, find_map_rows, find_rejections, load_schema,
                      normalize, detect_side, render)
from bco_brain import ROLE_HELP, STRATEGIES, Commander, decide_all, defense_posts, get_strategy, summary_line

DATA_DIR = os.environ.get("BCO_DATA_DIR", os.path.join(os.getcwd(), "bco-data"))


# ---------------------------------------------------------------- helpers
def match_dir(code):
    d = os.path.join(DATA_DIR, str(code))
    os.makedirs(d, exist_ok=True)
    return d


def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(obj):
    print(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2))


def match_list(resp):
    if isinstance(resp, list):
        return resp
    for k in ("matches", "data", "items", "results"):
        if isinstance(resp, dict) and isinstance(resp.get(k), list):
            return resp[k]
    return []


def match_code(m):
    return next((str(m[k]) for k in ("code", "id", "match_code", "slug") if isinstance(m, dict) and m.get(k) is not None), None)


def resolve_code(client, code=None):
    if code and code != "-":
        return code
    playing = [m for m in match_list(client.matches()) if isinstance(m, dict) and m.get("playing")]
    if not playing:
        sys.exit("Không có trận nào playing=true. Truyền mã phòng.")
    return match_code(playing[0])


def side_file(code):
    return os.path.join(DATA_DIR, str(code), "side.txt")


def cached_side(code):
    p = side_file(code)
    return open(p, encoding="utf-8").read().strip() if os.path.exists(p) else None


def fetch_info(client, code):
    """GET /matches/{code} (with X-Agent-Model) → save match.json and side.txt. Returns (info, side)."""
    info = client.match(code)
    save_json(os.path.join(match_dir(code), "match.json"), info)
    side = detect_side(info)
    if side:
        open(side_file(code), "w", encoding="utf-8").write(side)
    return info, side


def parse_params(pairs):
    return {k.strip(): v.strip() for k, _, v in (p.partition("=") for p in pairs or [])}


def parse_step(tok):
    parts = tok.split(":")
    step = {"tank": int(parts[0]) if parts[0].isdigit() else parts[0], "do": parts[1]}
    if len(parts) > 2:
        step["direction"] = parts[2]
    if len(parts) > 3:
        step["count" if parts[1] == "fire" else "cells"] = int(parts[3])
    return step


def print_game(g):
    print(f"side={g.my_side} status={g.status} time_left={g.time_left} armor={g.armor_active} "
          f"score={g.my_score}-{g.enemy_score} base={g.my_base_hp}-{g.enemy_base_hp} map={g.grid.W}x{g.grid.H} items={len(g.items)}")
    for t in g.my_tanks:
        print(f"  ta   T{t.id} ({t.x},{t.y}) dir={t.dir} lives={t.lives} stars={t.stars} busy={t.busy}")
    for t in g.enemy_tanks:
        print(f"  địch T{t.id} ({t.x},{t.y}) dir={t.dir} lives={t.lives} shield={t.shield}")
    for w in g.warnings:
        print("  !", w)


# ---------------------------------------------------------------- commands
def cmd_matches(a):
    for m in match_list(Client().matches()):
        if a.playing and not m.get("playing"):
            continue
        print(match_code(m) if a.playing else (match_code(m), {k: v for k, v in m.items() if k in ("status", "playing", "opponent", "side")}))


def cmd_info(a):
    c = Client(model=a.model)
    code = resolve_code(c, a.code)
    info, side = fetch_info(c, code)
    dump(info)
    print(f"\n# phòng={code} side={side or 'KHÔNG RÕ — đọc JSON và dùng --side'}")


def cmd_rules(a):
    c = Client()
    code = resolve_code(c, a.code)
    rules = c.rules(code)
    save_json(os.path.join(match_dir(code), "rules.json"), rules)
    dump(rules)


def cmd_state(a):
    c = Client()
    code = resolve_code(c, a.code)
    st = c.state(code)
    save_json(os.path.join(match_dir(code), "state-latest.json"), st)
    if a.summary:
        print_game(normalize(st, a.side or cached_side(code)))
    else:
        dump(st)
    print(f"# đã lưu {os.path.join(match_dir(code), 'state-latest.json')}")


def cmd_probe(a):
    state = load_json(a.file)

    def walk(node, path, depth):
        if depth > 6:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, depth + 1)
        elif isinstance(node, list):
            print(f"{path:45s} list[{len(node)}]")
            if node and not all(isinstance(x, str) for x in node):
                walk(node[0], f"{path}.0", depth + 1)
        else:
            r = repr(node)
            print(f"{path:45s} {r[:60] + ('…' if len(r) > 60 else '')}")

    print("== Các trường trong state ==")
    walk(state, "", 0)
    schema = load_schema()
    g = normalize(state, a.side, schema)
    print("\n== Adapter tìm thấy ==")
    for k, v in sorted(g.found.items()):
        print(f"  {k:14s} ← {v}")
    keys = ("status", "time_left", "armor_left", "my_tanks", "enemy_tanks", "my_base_hp", "enemy_base_hp",
            "my_score", "enemy_score", "events", "items", "map")
    print("\n== Chưa tìm thấy (điền trong references/state-schema.json) ==\n  " + (", ".join(k for k in keys if k not in g.found) or "không có"))
    rows = find_map_rows(state, schema)
    if rows:
        legend = schema.get("legend", {})
        print(f"\n== Bản đồ {len(rows[0])}x{len(rows)} ==")
        for ch, n in collections.Counter("".join(rows)).most_common():
            print(f"  '{ch}': {n:5d} → {legend.get(ch, 'CHƯA CÓ TRONG LEGEND')}")
    print("\n== Kết quả chuẩn hoá ==")
    print_game(g)
    if a.write:
        changed = [k for k, p in g.found.items() if k in schema and schema[k] is None and "[" not in p]
        for k in changed:
            schema[k] = g.found[k]
        save_json(SCHEMA_PATH, schema)
        print(f"\n# đã ghi {changed} vào {SCHEMA_PATH}")


def cmd_analyze(a):
    g = normalize(load_json(a.file), a.side)
    params = parse_params(a.param)
    if not a.no_map:
        ov = {t.pos: str(t.id)[-1] for t in g.my_tanks}
        ov.update({t.pos: "abcdefgh"[(int(t.id) - 1) % 8] if str(t.id).isdigit() else "e" for t in g.enemy_tanks})
        ov.update({g.my_base: "B", g.enemy_base: "X"})
        ov.update({(i.x, i.y): "*" for i in g.items})
        for p in defense_posts(g, {}):
            ov.setdefault(p, "+")
        print(render(g.grid, ov))
        print("1-4 xe ta (ô trên-trái) · a-d xe địch · B nhà ta · X nhà địch · * vật phẩm · + chốt thủ\n")
    print_game(g)
    for name in a.strategy or sorted(STRATEGIES):
        mem = {}
        decisions = decide_all(get_strategy(name, **params), g, mem)
        print(f"\n== {name}{' (phase=' + mem['adaptive_phase'] + ')' if mem.get('adaptive_phase') else ''} ==")
        for tid, d in sorted(decisions.items(), key=lambda kv: str(kv[0])):
            steps = " ; ".join(f"{s['do']} {s.get('direction', '')} {s.get('cells', s.get('count', ''))}".strip() for s in d.steps)
            print(f"  T{tid}: {d.job:45s} {steps or '(giữ nguyên)'}{'  [GẤP]' if d.urgent else ''}")


def cmd_strategies(a):
    for name, cls in sorted(STRATEGIES.items()):
        print(f"■ {name}: {cls.summary}")
        for k, v in cls.params_help.items():
            print(f"    --param {k}=…  {v}")
    print("\nVai (dùng trong plan.json / --param roles=):")
    for k, v in ROLE_HELP.items():
        print(f"  {k:24s} {v}")


def cmd_plan(a):
    c = Client()
    code = resolve_code(c, a.code)
    steps = json.loads(a.json)["steps"] if a.json else [parse_step(t) for t in a.steps]
    if not steps:
        sys.exit("Chưa có bước nào.")
    try:
        resp = c.plan(code, steps)
    except ApiError as e:
        resp = e.body
        print(f"HTTP {e.status}")
    dump(resp)
    for t, r in find_rejections(resp, [s["tank"] for s in steps]).items():
        print(f"# XE BỊ TỪ CHỐI T{t}: {r} → sửa rồi gửi lại riêng xe này")


def cmd_order(a):
    c = Client()
    code = resolve_code(c, a.code)
    step = {"tank": a.tank, "do": a.do}
    if a.direction:
        step["direction"] = a.direction
    if a.n is not None:
        step["count" if a.do == "fire" else "cells"] = a.n
    try:
        dump(c.order(code, step))
    except ApiError as e:
        print(f"HTTP {e.status}")
        dump(e.body)


def cmd_stop(a):
    c = Client()
    code = resolve_code(c, a.code)
    for t in ([1, 2, 3, 4] if a.tanks == ["all"] else [int(x) for x in a.tanks]):
        dump({t: c.stop(code, t)})


def cmd_run(a):
    client = Client()
    code = resolve_code(client, a.code)
    mdir = match_dir(code)
    side = None
    try:  # always call this first: the room's Ready button unlocks once the agent called the API
        _, side = fetch_info(client, code)
    except ApiError as e:
        print("! GET /matches/{code} lỗi:", e, file=sys.stderr)
    side = a.side or side or cached_side(code)
    if not side:
        sys.exit("Không đoán được bên top/bottom. Chạy lại với --side.")
    if not os.path.exists(os.path.join(mdir, "rules.json")):
        try:
            save_json(os.path.join(mdir, "rules.json"), client.rules(code))
        except ApiError as e:
            print("! không đọc được rules:", e, file=sys.stderr)

    def with_defaults(name, prm):
        return dict(prm, plan=os.path.join(mdir, "plan.json")) if name == "scripted" and "plan" not in prm else prm

    name, params = a.strategy, parse_params(a.param)
    if a.plan:
        name, params["plan"] = "scripted", os.path.abspath(a.plan)
    params = with_defaults(name, params)
    cmd = Commander(client, code, get_strategy(name, **params), dry_run=a.dry_run, refresh_s=a.refresh,
                    log_path=os.path.join(mdir, "log.jsonl"), inline_stop=not a.separate_stop)
    print(f"# phòng={code} side={side} chiến thuật={name} {params} dry_run={a.dry_run}", flush=True)

    control = os.path.join(mdir, "control.json")
    ctl_mtime, paused, errors, waiting_logged = None, False, 0, False
    interval, seen429, last429 = a.interval, 0, -1e9  # adaptive pace: slow down on 429, recover after 10s
    deadline = time.time() + a.max_minutes * 60
    while time.time() < deadline:
        t0 = time.time()
        if os.path.exists(control) and os.path.getmtime(control) != ctl_mtime:  # hot reload
            ctl_mtime = os.path.getmtime(control)
            try:
                ctl = load_json(control)
                if ctl.get("quit"):
                    print("# dừng theo control.json")
                    break
                paused = bool(ctl.get("pause"))
                if ctl.get("strategy"):
                    new = with_defaults(ctl["strategy"], {k: str(v) for k, v in ctl.get("params", {}).items()})
                    if (ctl["strategy"], new) != (name, params):
                        name, params = ctl["strategy"], new
                        cmd.strategy = get_strategy(name, **params)
                        for tm in cmd.tanks.values():
                            tm["job"] = None
                        print(f"# đổi chiến thuật → {name} {params}", flush=True)
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
            print(f"# TRẬN KẾT THÚC điểm {game.my_score}-{game.enemy_score} nhà {game.my_base_hp}-{game.enemy_base_hp}", flush=True)
            break
        if game.waiting:
            if a.once:
                print(f"# trận chưa bắt đầu (status={game.status})")
                break
            if not waiting_logged:
                print(f"# chờ trận bắt đầu (status={game.status})... bấm Sẵn sàng trên trang phòng", flush=True)
                waiting_logged = True
            time.sleep(1)
            continue
        if paused:
            time.sleep(interval)
            continue
        if client.n429 > seen429:
            seen429, last429 = client.n429, time.time()
            interval = min(2.0, interval * 1.5)
            print(f"# 429 — giãn nhịp lên {interval:.2f}s", flush=True)
        elif interval > a.interval and time.time() - last429 > 10:
            interval = max(a.interval, interval / 1.25)
        report, rejected = cmd.step(game)
        if not a.quiet:
            print(summary_line(game, report, rejected, cmd.memory), flush=True)
        if a.once:
            for tid, r in report.items():
                print(f"  T{tid}: {r}")
            break
        time.sleep(max(0.02, interval - (time.time() - t0)))


def cmd_watch(a):
    d = os.path.join(DATA_DIR, a.code)
    log_path, state_path = os.path.join(d, "log.jsonl"), os.path.join(d, "state-latest.json")
    pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    recs, alerts, first, t_end = [], [], None, time.time() + a.seconds
    while time.time() < t_end:
        new = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                f.seek(pos)
                for line in f:
                    try:
                        new.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                pos = f.tell()
        recs += new
        first = first or (recs[0] if recs else None)
        for r in new:
            if first and None not in (r["base_hp"][0], first["base_hp"][0]) and r["base_hp"][0] < first["base_hp"][0]:
                alerts.append(f"NHÀ TA MẤT MÁU: {first['base_hp'][0]}→{r['base_hp'][0]}")
                first = r
            if r.get("rejected"):
                alerts.append(f"bị từ chối: {r['rejected']}")
        if any(x.startswith("NHÀ TA") for x in alerts) or sum(x.startswith("bị từ chối") for x in alerts) >= 3:
            break
        time.sleep(1)

    try:
        state = load_json(state_path)
    except (OSError, json.JSONDecodeError):
        state = {}
    st = str(state.get("status", "")).lower() if isinstance(state, dict) else ""
    finished = st in ("finished", "ended", "over", "done")
    if finished:
        print("TRẬN KẾT THÚC · " + json.dumps({k: state.get(k) for k in ("status", "winner", "result", "reason") if state.get(k) is not None}, ensure_ascii=False))
    if recs:
        r = recs[-1]
        jobs = " | ".join(f"T{k}:{v.get('job', '').split('|')[-1][:28]}" for k, v in sorted(r["decisions"].items()))
        print(f"t={r['time_left']} giáp={'Y' if r['armor'] else 'N'} điểm {r['score'][0]}-{r['score'][1]} nhà {r['base_hp'][0]}-{r['base_hp'][1]} "
              f"{'phase=' + r['phase'] if r.get('phase') else ''} chiến thuật={r['strategy']} · {len(recs)} nhịp/{a.seconds:.0f}s")
        print("  xe ta:", r["tanks"], " địch:", r["enemies"])
        print("  việc:", jobs)
        for w in sorted(set(r.get("warnings") or [])):
            print("  ! cảnh báo:", w)
    for x in dict.fromkeys(alerts):
        print("  !", x)
    if not finished and not recs:
        if os.path.exists(state_path) and time.time() - os.path.getmtime(state_path) < 10:
            print("trận vừa bắt đầu — gọi watch lại" if st in ("live", "playing", "running")
                  else f"chờ trận bắt đầu (status={st or '?'}) — người dùng cần bấm Sẵn sàng")
            sys.exit(0)
        print("! không có nhịp mới — runner (bco.py run) có đang chạy không? Chạy lại nếu đã chết.")
        sys.exit(4)
    sys.exit(3 if finished else 0)


# ---------------------------------------------------------------- argparse
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("matches"); p.add_argument("--playing", action="store_true"); p.set_defaults(fn=cmd_matches)
    p = sub.add_parser("info"); p.add_argument("code", nargs="?"); p.add_argument("--model"); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("rules"); p.add_argument("code", nargs="?"); p.set_defaults(fn=cmd_rules)
    p = sub.add_parser("state"); p.add_argument("code", nargs="?"); p.add_argument("--summary", action="store_true")
    p.add_argument("--side", choices=["top", "bottom"]); p.set_defaults(fn=cmd_state)
    p = sub.add_parser("probe"); p.add_argument("file"); p.add_argument("--side", choices=["top", "bottom"])
    p.add_argument("--write", action="store_true"); p.set_defaults(fn=cmd_probe)
    p = sub.add_parser("analyze"); p.add_argument("file"); p.add_argument("--side", choices=["top", "bottom"])
    p.add_argument("--strategy", action="append"); p.add_argument("--param", action="append", default=[])
    p.add_argument("--no-map", action="store_true"); p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("strategies"); p.set_defaults(fn=cmd_strategies)
    p = sub.add_parser("plan"); p.add_argument("code"); p.add_argument("steps", nargs="*"); p.add_argument("--json")
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("order"); p.add_argument("code"); p.add_argument("tank", type=int)
    p.add_argument("do", choices=["move", "move_fire", "turn", "fire", "stop"])
    p.add_argument("direction", nargs="?", choices=["up", "right", "down", "left"]); p.add_argument("n", nargs="?", type=int)
    p.set_defaults(fn=cmd_order)
    p = sub.add_parser("stop"); p.add_argument("code"); p.add_argument("tanks", nargs="+"); p.set_defaults(fn=cmd_stop)
    p = sub.add_parser("run"); p.add_argument("code", nargs="?")
    p.add_argument("--strategy", default="adaptive"); p.add_argument("--plan", help="file plan.json → chiến thuật scripted")
    p.add_argument("--param", action="append", default=[]); p.add_argument("--side", choices=["top", "bottom"])
    p.add_argument("--interval", type=float, default=0.5, help="giây/nhịp (server 0,5s); tự giãn khi gặp 429"); p.add_argument("--refresh", type=float, default=4.0)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--once", action="store_true")
    p.add_argument("--max-minutes", type=float, default=40); p.add_argument("--quiet", action="store_true")
    p.add_argument("--separate-stop", action="store_true", help="gọi POST /stop riêng thay vì đặt stop đầu /plan")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("watch"); p.add_argument("code"); p.add_argument("--seconds", type=float, default=25)
    p.set_defaults(fn=cmd_watch)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
