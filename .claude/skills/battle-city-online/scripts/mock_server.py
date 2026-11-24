#!/usr/bin/env python3
"""Server giả lập cục bộ để thử chiến thuật khi chưa có trận thật (KHÔNG phải engine thật).

Mô phỏng thô: xe 2x2, đi 1 ô/nhịp, bắn tức thời theo làn, gạch vỡ, đại bàng bất tử 5s, xe trúng
đại bàng bị đưa về nhà, vật phẩm, giáp đầu trận. Địch là bot ngẫu nhiên (bắn khi thẳng hàng).

  python3 mock_server.py --port 8765 --speed 4 --armor 0 &
  BCO_TOKEN=test BCO_BASE_URL=http://127.0.0.1:8765/api/v1 python3 run_strategy.py TEST1 --strategy siege
"""
import argparse
import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bco_lib.grid import DELTA, DIRS, trace

HERE = os.path.dirname(os.path.abspath(__file__))
S = 2

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--port", type=int, default=8765)
ap.add_argument("--map", default=os.path.join(HERE, "fixtures", "sample_map.txt"))
ap.add_argument("--side", default="bottom", choices=["top", "bottom"], help="bên của người chơi (agent)")
ap.add_argument("--speed", type=float, default=1.0, help="tăng tốc thời gian (4 = nhanh gấp 4)")
ap.add_argument("--armor", type=float, default=180, help="giây game có giáp đầu trận")
ap.add_argument("--game-seconds", type=float, default=480)
ap.add_argument("--enemy", default="random", choices=["random", "idle"])
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--start-delay", type=float, default=0, help="giây thực ở trạng thái scheduled trước khi live")
ap.add_argument("--dump-state", help="ghi state ban đầu ra file rồi thoát")
a = ap.parse_args()
random.seed(a.seed)

LEGEND = {".": "empty", "#": "brick", "@": "steel", "%": "tree", "~": "water"}
CHAR = {v: k for k, v in LEGEND.items()}
rows = [list(r) for r in open(a.map, encoding="utf-8").read().split("\n") if r]
H, W = len(rows), len(rows[0])
BASE = {"top": (W // 2 - 1, 0), "bottom": (W // 2 - 1, H - 2)}
SPAWNS = {"top": [(4, 0), (12, 0), (26, 0), (34, 0)], "bottom": [(4, H - 2), (12, H - 2), (26, H - 2), (34, H - 2)]}
OPP = {"top": "bottom", "bottom": "top"}
lock = threading.Lock()


class G:  # grid view for bco_lib.trace
    W, H = W, H

    @staticmethod
    def at(x, y):
        if 0 <= x < W and 0 <= y < H:
            return LEGEND.get(rows[y][x], "empty")
        return "wall"


world = {
    "t": 0.0, "status": "live", "events": [], "items": [], "next_item": 20.0,
    "sides": {s: {"score": 0, "base_hp": 5, "eagle_inv_until": -1,
                  "tanks": [{"id": i + 1, "x": p[0], "y": p[1], "dir": "down" if s == "top" else "up", "lives": 15,
                             "stars": 0, "order": [], "shield_until": 1.5, "dead_until": -1} for i, p in enumerate(SPAWNS[s])]}
              for s in ("top", "bottom")},
}


def all_tanks():
    return [(s, t) for s in ("top", "bottom") for t in world["sides"][s]["tanks"]]


def fp_free(x, y, me=None):
    for j in range(S):
        for i in range(S):
            if G.at(x + i, y + j) not in ("empty", "tree"):
                return False
    for s in BASE.values():
        if abs(s[0] - x) < S and abs(s[1] - y) < S:
            return False
    for _, t in all_tanks():
        if t is not me and t["dead_until"] < world["t"] and abs(t["x"] - x) < S and abs(t["y"] - y) < S:
            return False
    return True


def front_cells(t, d):
    dx, dy = DELTA[d]
    if dy:
        r = t["y"] - 1 if dy < 0 else t["y"] + S
        return [(t["x"] + i, r) for i in range(S)]
    c = t["x"] - 1 if dx < 0 else t["x"] + S
    return [(c, t["y"] + j) for j in range(S)]


def send_home(side, t):
    sp = SPAWNS[side][t["id"] - 1]
    t.update(x=sp[0], y=sp[1], order=[])


def fire(side, t, d):
    t["dir"] = d
    ents = [("base_" + s, BASE[s], s) for s in BASE]
    ents += [("tank", (o["x"], o["y"]), (os_, o)) for os_, o in all_tanks() if o is not t and o["dead_until"] < world["t"]]
    hit = trace(G, (t["x"], t["y"]), d, S, ents, t["stars"] >= 3)
    if hit["bricks"]:  # bullet stops at the first brick row and breaks it
        x, y = t["x"], t["y"]
        dx, dy = DELTA[d]
        for step in range(1, 60):
            cells = [(x + i, y - step if dy < 0 else y + S - 1 + step) for i in range(S)] if dy else \
                    [(x - step if dx < 0 else x + S - 1 + step, y + j) for j in range(S)]
            if any(G.at(cx, cy) == "brick" or (t["stars"] >= 3 and G.at(cx, cy) == "steel") for cx, cy in cells):
                for cx, cy in cells:
                    if G.at(cx, cy) in ("brick",) or (t["stars"] >= 3 and G.at(cx, cy) == "steel"):
                        rows[cy][cx] = "."
                return
    k = hit["kind"]
    if k.startswith("base_"):
        bs = hit["obj"]
        b = world["sides"][bs]
        in_enemy_half = (t["y"] <= 19) if side == "bottom" else (t["y"] >= 21)
        send_home(side, t)
        world["events"].append({"type": "eagle_hit", "tank": t["id"], "side": side, "target": bs})
        if world["t"] < a.armor or world["t"] < b["eagle_inv_until"]:
            return
        if bs != side and not in_enemy_half:
            return
        b["base_hp"] -= 1
        b["eagle_inv_until"] = world["t"] + 5
        if bs != side:
            world["sides"][side]["score"] += 1000
    elif k == "tank":
        os_, o = hit["obj"]
        if o["shield_until"] > world["t"]:
            return
        o["lives"] -= 1
        o["dead_until"] = world["t"] + 1.5
        o["shield_until"] = world["t"] + 3
        o["stars"] = 0
        send_home(os_, o)
        world["events"].append({"type": "tank_destroyed", "tank": o["id"], "side": os_, "by": t["id"]})
        if os_ != side:
            world["sides"][side]["score"] += 500


def exec_step(side, t):
    if not t["order"] or t["dead_until"] >= world["t"]:
        return
    st = t["order"][0]
    do, d = st["do"], st.get("direction") or t["dir"]
    if do == "turn":
        t["dir"] = d
        t["order"].pop(0)
    elif do == "fire":
        t["order"].pop(0)
        for _ in range(int(st.get("count", 1))):
            fire(side, t, d)
            if t["dir"] != d or (t["x"], t["y"]) in SPAWNS[side]:  # sent home: rest of volley lost
                break
    elif do in ("move", "move_fire"):
        t["dir"] = d
        dx, dy = DELTA[d]
        if do == "move_fire" and any(G.at(cx, cy) == "brick" for cx, cy in front_cells(t, d)):
            fire(side, t, d)
            return
        if fp_free(t["x"] + dx, t["y"] + dy, t):
            t["x"] += dx
            t["y"] += dy
            st["cells"] = int(st.get("cells", 1)) - 1
            if st["cells"] <= 0 and t["order"]:
                t["order"].pop(0)
        else:
            world["events"].append({"type": "order_failed", "tank": t["id"], "side": side, "reason": "blocked"})
            t["order"] = []


def enemy_ai(side):
    for t in world["sides"][side]["tanks"]:
        if t["order"] or t["dead_until"] >= world["t"] or a.enemy == "idle":
            continue
        for d in DIRS:
            ents = [("e", (o["x"], o["y"]), o) for o in world["sides"][OPP[side]]["tanks"]]
            ents.append(("own", BASE[side], None))
            h = trace(G, (t["x"], t["y"]), d, S, ents, max_range=26)
            if h["kind"] == "e" and h["bricks"] == 0:
                t["order"] = [{"do": "fire", "direction": d, "count": 1}]
                break
        else:
            d = random.choice(DIRS + ("down",) if side == "top" else DIRS + ("up",))
            t["order"] = [{"do": "move_fire", "direction": d, "cells": random.randint(1, 5)}]


def tick():
    world["t"] += 0.25  # 8:00 game clock = 16 real minutes
    if random.random() < 0.35:
        enemy_ai(OPP[a.side])
    for s, t in all_tanks():
        if t["dead_until"] < world["t"] and t["lives"] > 0:
            exec_step(s, t)
        for it in list(world["items"]):
            if abs(it["x"] - t["x"]) < S and abs(it["y"] - t["y"]) < S:
                world["items"].remove(it)
                world["sides"][s]["score"] += 200
                if it["type"] == "star":
                    t["stars"] = min(3, t["stars"] + 1)
                elif it["type"] == "life":
                    t["lives"] += 1
    world["items"] = [i for i in world["items"] if i["until"] > world["t"]]
    if world["t"] >= world["next_item"]:
        world["next_item"] += 20
        for _ in range(50):
            x, y = random.randrange(W - 1), random.randrange(4, H - 5)
            if fp_free(x, y):
                world["items"].append({"type": random.choice(["star", "star", "helmet", "shovel", "life"]), "x": x, "y": y, "until": world["t"] + 15})
                break
    for s in ("top", "bottom"):
        sd = world["sides"][s]
        if sd["base_hp"] <= 0 or sd["score"] >= 20000 or all(t["lives"] <= 0 for t in sd["tanks"]) or world["t"] >= a.game_seconds:
            world["status"] = "finished"


def ticker():
    if a.start_delay:
        world["status"] = "scheduled"
        time.sleep(a.start_delay)
        world["status"] = "live"
    while world["status"] == "live":
        time.sleep(0.5 / a.speed)  # command tick = 0.5 s real; game clock runs at half speed (see tick)
        with lock:
            tick()
    print("# MOCK KẾT THÚC", {s: (world["sides"][s]["score"], world["sides"][s]["base_hp"]) for s in world["sides"]}, flush=True)


def view():
    me, en = world["sides"][a.side], world["sides"][OPP[a.side]]
    left = max(0, a.game_seconds - world["t"])

    def tanks(sd, mine):
        return [{"id": t["id"], "x": t["x"], "y": t["y"], "dir": t["dir"], "lives": t["lives"], "stars": t["stars"],
                 "shield": t["shield_until"] > world["t"], "alive": t["lives"] > 0 and t["dead_until"] < world["t"],
                 **({"order": [dict(o) for o in t["order"]]} if mine else {})} for t in sd["tanks"]]
    ev, world["events"] = world["events"], []
    return {
        "status": world["status"], "time_left": f"{int(left // 60)}:{int(left % 60):02d}",
        "armor_left": max(0, a.armor - world["t"]),
        "you": {"side": a.side, "score": me["score"], "base_hp": me["base_hp"], "tanks": tanks(me, True)},
        "enemy": {"side": OPP[a.side], "score": en["score"], "base_hp": en["base_hp"], "tanks": tanks(en, False)},
        "items": [{"type": i["type"], "x": i["x"], "y": i["y"]} for i in world["items"]],
        "events": ev, "map": "\n".join("".join(r) for r in rows),
    }


def accept_plan(steps):
    results, by_tank = [], {}
    for st in steps:
        by_tank.setdefault(st.get("tank"), []).append(st)
    mine = {t["id"]: t for t in world["sides"][a.side]["tanks"]}
    for tid, sts in by_tank.items():
        t = mine.get(tid)
        err = None
        if t is None:
            err = "unknown tank"
        else:
            for st in sts:
                if st.get("do") not in ("move", "move_fire", "turn", "fire", "stop"):
                    err = f"bad do {st.get('do')}"
                elif st.get("do") != "stop" and st.get("direction") not in DIRS and st.get("do") != "fire":
                    err = "bad direction"
                elif st.get("do") == "fire" and not 1 <= int(st.get("count", 1)) <= 3:
                    err = "count must be 1-3"
            queue = list(t["order"])
            for st in sts:
                if st["do"] == "stop":
                    queue = []
                else:
                    queue.append(dict(st))
            if not err and len(queue) > 5:
                err = "too many queued steps (max 5)"
            if not err:
                t["order"] = [{k: v for k, v in s.items() if k != "tank"} for s in queue]
        results.append({"tank": tid, "ok": err is None, **({"error": err} if err else {})})
    return {"results": results}


class H_(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0].removeprefix("/api/v1")
        with lock:
            if p == "/matches":
                return self._send(200, {"matches": [{"code": "TEST1", "playing": world["status"] == "live", "status": world["status"]}]})
            if p == "/matches/TEST1":
                return self._send(200, {"code": "TEST1", "you": {"side": a.side}, "mode": "duel", "tanks_per_side": 4})
            if p == "/matches/TEST1/rules":
                return self._send(200, {"rules": "mock rules — see references/quick-reference.md"})
            if p == "/matches/TEST1/state":
                return self._send(200, view())
        self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.removeprefix("/api/v1")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with lock:
            if p == "/matches/TEST1/plan":
                return self._send(200, accept_plan(body.get("steps", [])))
            if p == "/matches/TEST1/orders":
                r = accept_plan([body])["results"][0]
                return self._send(200 if r["ok"] else 422, r)
            if p == "/matches/TEST1/stop":
                for t in world["sides"][a.side]["tanks"]:
                    if t["id"] == body.get("tank"):
                        t["order"] = []
                return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


if a.dump_state:
    json.dump(view(), open(a.dump_state, "w"), indent=2)
    raise SystemExit(0)
threading.Thread(target=ticker, daemon=True).start()
print(f"# mock server http://127.0.0.1:{a.port}/api/v1  match=TEST1 side={a.side} speed×{a.speed}", flush=True)
ThreadingHTTPServer(("127.0.0.1", a.port), H_).serve_forever()
