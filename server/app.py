#!/usr/bin/env python3
"""Battle City Online — server luyện tập tự dựng (chỉ dùng thư viện chuẩn Python 3.9+).

  python server/app.py                      # http://localhost:8080
  python server/app.py --host 0.0.0.0 --port 8080   # cho cả mạng LAN vào

Agent dùng API giống server thật: BCO_BASE_URL=http://localhost:8080/api/v1
"""
import argparse
import json
import os
import random
import re
import secrets
import string
import threading
import time
import traceback
from collections import deque
from dataclasses import fields
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from engine import Bot, Config, Game, MAPS, OPP, SIDE_VI

HERE = os.path.dirname(os.path.abspath(__file__))
LOCK = threading.RLock()
ROOMS = {}
RATE_LIMIT_PER_S = 15


# ----------------------------------------------------------------------------- users
class Users:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def login(self, name):
        if name not in self.data:
            self.data[name] = {"token": secrets.token_hex(16), "created": time.time()}
            self.save()
        return self.data[name]["token"]

    def new_token(self, name):
        self.data[name]["token"] = secrets.token_hex(16)
        self.save()
        return self.data[name]["token"]

    def by_token(self, token):
        for name, u in self.data.items():
            if token and secrets.compare_digest(u["token"], token):
                return name
        return None


USERS = None
RATE = {}


# ----------------------------------------------------------------------------- rooms
class Room:
    def __init__(self, owner, name, bot=None, cfg=None):
        self.code = self.new_code()
        self.name = name or f"Phòng của {owner}"
        self.owner = owner
        self.bot_level = bot
        self.cfg = cfg or Config()
        self.players = {"bottom": owner, "top": f"Bot ({bot})" if bot else None}
        self.ready = {"bottom": False, "top": bool(bot)}
        self.agent_seen = {"bottom": False, "top": bool(bot)}
        self.agent_model = {"bottom": None, "top": "bot" if bot else None}
        self.countdown_until = None
        self.cursor = {}                       # user -> last event seq returned by /state
        self.agent_log = {}                    # user -> deque of API calls
        self.history = []
        self.created = time.time()
        self.new_game()

    @staticmethod
    def new_code():
        while True:
            c = "".join(random.choice(string.ascii_uppercase) for _ in range(4))
            if c not in ROOMS:
                return c

    def new_game(self):
        self.game = Game(self.cfg)
        self.bot = Bot(self.game, "top", self.bot_level) if self.bot_level else None
        self.cursor = {}
        self.countdown_until = None
        self.ready = {"bottom": False, "top": bool(self.bot_level)}

    def side_of(self, user):
        for s, p in self.players.items():
            if p == user:
                return s
        return None

    @property
    def status(self):
        if self.countdown_until:
            return "countdown"
        return self.game.status

    def meta(self, viewer=None):
        g = self.game
        return {
            "code": self.code, "name": self.name, "owner": self.owner, "status": self.status,
            "players": self.players, "ready": self.ready, "agent_seen": self.agent_seen, "agent_model": self.agent_model,
            "bot": self.bot_level, "countdown": round(self.countdown_until - time.time(), 1) if self.countdown_until else None,
            "config": self.cfg.rules(), "map": g.map_name, "you": self.side_of(viewer) if viewer else None,
            "history": self.history[-10:],
        }

    def log_call(self, user, entry):
        self.agent_log.setdefault(user, deque(maxlen=500)).append(entry)


def tick_loop():
    last = time.time()
    while True:
        time.sleep(0.05)
        now = time.time()
        dt, last = min(0.2, now - last), now
        with LOCK:
            for room in list(ROOMS.values()):
                g = room.game
                if room.countdown_until and now >= room.countdown_until:
                    room.countdown_until = None
                    g.start()
                if g.status == "live":
                    try:
                        g.step(dt * room.cfg.time_scale)
                        if room.bot:
                            room.bot.tick()
                    except Exception:  # one broken match must never freeze the other rooms
                        traceback.print_exc()
                        g.status, g.reason = "finished", "lỗi server (xem log) — trận bị huỷ"
                        g.winner = g.winner or "bottom"
                        g.emit({"type": "match_finished", "winner": None, "reason": g.reason, "text": "Trận bị huỷ do lỗi server"})
                    if g.status == "finished":
                        room.history.append({"winner": room.players[g.winner], "side": g.winner, "reason": g.reason,
                                             "score": dict(g.score), "map": g.map_name,
                                             "at": datetime.now().strftime("%H:%M:%S")})
            # drop empty, old rooms
            for code, room in list(ROOMS.items()):
                if room.players["bottom"] is None and room.players["top"] in (None,) and time.time() - room.created > 600:
                    ROOMS.pop(code, None)


def cfg_from(d, base=None):
    cfg = Config(**(base.rules() if base else {}))
    for f in fields(Config):
        if f.name in (d or {}):
            try:
                setattr(cfg, f.name, type(getattr(cfg, f.name))(d[f.name]))
            except (TypeError, ValueError):
                pass
    cfg.tanks = max(1, min(4, cfg.tanks))
    cfg.time_scale = max(0.25, min(8.0, cfg.time_scale))
    return cfg


RULES_TEXT = [
    "Duel: không có xe NPC, phá được căn cứ đối phương là thắng.",
    "Xe 2x2 ô, toạ độ (x, y) là ô trên-trái; x sang phải, y xuống dưới.",
    "Lệnh: move/move_fire (direction, cells), turn (direction), fire (count 1-3, direction), stop.",
    "Lệnh mới nối vào cuối kế hoạch; đổi việc thì đặt {tank, do: stop} lên đầu. Tối đa 5 bước chờ mỗi xe.",
    "Lệnh hai bên được áp dụng cùng lúc ở đầu nhịp sau (nhịp 0,5 giây).",
    "Giáp 3:00 đầu trận: tường quanh hai đại bàng là thép, chỉ xe 3 sao bắn vỡ.",
    "Căn cứ chỉ mất máu khi bị bắn từ chính nửa sân của nó (dưới: y ≥ 21; trên: y ≤ 19).",
    "Bắn trúng đại bàng địch: xe bị đưa về điểm xuất phát (giữ sao, mạng; mất kế hoạch). Đại bàng bất tử 5 giây.",
    "Điểm: 1000/máu đại bàng · 500/xe hạ · 200/vật phẩm. Đạt mốc điểm trước là thắng.",
    "Xe bị hạ hồi sinh sau 3 giây cạnh căn cứ, khiên 3 giây, mất hết sao và mất việc.",
    "Vật phẩm: mỗi 20 giây một cặp ở hai vị trí đối xứng, tồn tại 15 giây. ⭐ ⛑️ ⛏️ 🛡️",
    "Bản đồ ASCII: . trống · # gạch · @ thép · ~ nước · % bụi · - băng · 1-4 xe bạn · O xe địch · H nhà bạn · T nhà địch · P vật phẩm.",
]


# ----------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "BCO-practice/1.0"

    def log_message(self, fmt, *args):
        pass

    # --- plumbing
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if "json" in ctype or "html" in ctype else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if code == 429:
            self.send_header("Retry-After", "1")
        self.end_headers()
        self.wfile.write(body)
        return code, obj

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Body không phải JSON hợp lệ")

    def _user(self):
        auth = self.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
        return USERS.by_token(token)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method):
        url = urlparse(self.path)
        path, qs = url.path.rstrip("/") or "/", parse_qs(url.query)
        try:
            if path.startswith("/api/v1"):
                return self._api(method, path[len("/api/v1"):] or "/", qs)
            if path.startswith("/web"):
                return self._web(method, path[len("/web"):], qs)
            if method == "GET" and path in ("/", "/index.html") or path.startswith("/room/"):
                with open(os.path.join(HERE, "static", "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html")
            return self._send(404, {"error": "không tìm thấy"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})

    # --- agent API (same shape as the real server's documented endpoints)
    def _api(self, method, path, qs):
        user = self._user()
        if not user:
            return self._send(401, {"error": "Thiếu hoặc sai token. Gửi header Authorization: Bearer $BCO_TOKEN"})
        now = time.time()
        q = RATE.setdefault(user, deque())
        while q and now - q[0] > 1.0:
            q.popleft()
        if len(q) >= RATE_LIMIT_PER_S:
            return self._send(429, {"error": f"Gọi quá {RATE_LIMIT_PER_S} lần/giây. Gộp lệnh vào một POST /plan."})
        q.append(now)

        body = self._body() if method == "POST" else None
        with LOCK:
            if path == "/matches" and method == "GET":
                mine = []
                for r in ROOMS.values():
                    side = r.side_of(user)
                    if side:
                        mine.append({"code": r.code, "name": r.name, "status": r.status, "side": side,
                                     "playing": r.status in ("scheduled", "countdown", "live"),
                                     "opponent": r.players[OPP[side]], "map": r.game.map_name})
                return self._send(200, {"matches": mine})
            m = re.fullmatch(r"/matches/([A-Za-z0-9]+)(/[a-z]+)?", path)
            if not m:
                return self._send(404, {"error": f"Không có endpoint {method} {path}"})
            code, sub = m.group(1).upper(), (m.group(2) or "")
            room = ROOMS.get(code)
            side = room.side_of(user) if room else None
            if not side:
                yours = [r.code for r in ROOMS.values() if r.side_of(user) and r.status != "finished"]
                return self._send(404, {"error": f"Phòng {code} không tồn tại hoặc bạn không ở trong phòng này",
                                        "your_rooms": yours, "current_room": yours[0] if yours else None})
            room.agent_seen[side] = True
            if self.headers.get("X-Agent-Model"):
                room.agent_model[side] = self.headers.get("X-Agent-Model")[:60]
            code_, resp = self._match_api(method, sub, room, side, user, body, qs)
            room.log_call(user, {"t": datetime.now().strftime("%H:%M:%S.%f")[:-3], "method": method, "path": self.path,
                                 "status": code_, "req": json.dumps(body, ensure_ascii=False)[:400] if body else "",
                                 "resp": json.dumps(resp, ensure_ascii=False)[:400]})
            return code_, resp

    def _match_api(self, method, sub, room, side, user, body, qs):
        g = room.game
        if method == "GET" and sub == "":
            return self._send(200, {"code": room.code, "status": room.status, "map": g.map_name,
                                    "you": {"name": user, "side": side, "color": SIDE_VI[side]},
                                    "opponent": {"name": room.players[OPP[side]], "side": OPP[side], "is_bot": bool(room.bot_level),
                                                 "level": room.bot_level},
                                    "config": room.cfg.rules()})
        if method == "GET" and sub == "/rules":
            return self._send(200, {"code": room.code, "config": room.cfg.rules(), "rules": RULES_TEXT})
        if method == "GET" and sub == "/state":
            with_map = qs.get("map", ["false"])[0].lower() in ("1", "true", "yes")
            st, seq = g.state_for(side, room.cursor.get(user, 0), with_map)
            room.cursor[user] = seq
            st["code"] = room.code
            if g.status == "finished":
                st["note"] = f"room {room.code} is over (finished): it takes no more orders"
            if room.status == "countdown":
                st["status"] = "scheduled"
                st["starts_in"] = round(room.countdown_until - time.time(), 1)
            return self._send(200, st)
        if method == "POST" and sub in ("/plan", "/orders", "/stop"):
            if g.status != "live":
                return self._send(409, {"error": f"Trận chưa bắt đầu hoặc đã kết thúc (status={room.status})", "status": room.status})
            if sub == "/plan":
                steps = (body or {}).get("steps")
                if not isinstance(steps, list) or not steps:
                    return self._send(400, {"error": 'Body phải là {"steps": [{"tank": 1, "do": "...", ...}, ...]}'})
                res = g.submit(side, steps)
            elif sub == "/orders":
                res = g.submit(side, [body or {}])
            else:
                res = g.submit(side, [{"tank": (body or {}).get("tank"), "do": "stop"}])
            rejected = {str(r["tank"]): r["error"] for r in res if not r["ok"]}
            payload = {"results": res, "accepted": [r["tank"] for r in res if r["ok"]], "rejected": rejected,
                       "applies_at_next_tick": True}
            return self._send(200 if sub == "/plan" or not rejected else 422, payload)
        return self._send(404, {"error": f"Không có endpoint {method} /matches/{room.code}{sub}"})

    # --- web UI endpoints
    def _web(self, method, path, qs):
        body = self._body() if method == "POST" else {}
        if path == "/login" and method == "POST":
            name = str(body.get("name", "")).strip()[:24]
            if not re.fullmatch(r"[\w .\-]{2,24}", name):
                return self._send(400, {"error": "Tên 2-24 ký tự (chữ, số, khoảng trắng, . _ -)"})
            with LOCK:
                return self._send(200, {"name": name, "token": USERS.login(name)})
        user = self._user()
        if not user:
            return self._send(401, {"error": "Chưa đăng nhập"})
        with LOCK:
            if path == "/me":
                return self._send(200, {"name": user, "token": USERS.data[user]["token"]})
            if path == "/token/new" and method == "POST":
                return self._send(200, {"name": user, "token": USERS.new_token(user)})
            if path == "/rooms" and method == "GET":
                rooms = sorted(ROOMS.values(), key=lambda r: -r.created)
                return self._send(200, {"rooms": [r.meta(user) for r in rooms], "maps": ["random"] + list(MAPS) + ["gen"]})
            if path == "/rooms" and method == "POST":
                bot = body.get("bot")
                if bot not in (None, "", "easy", "medium", "hard"):
                    return self._send(400, {"error": "bot phải là easy/medium/hard"})
                room = Room(user, str(body.get("name", ""))[:40], bot or None, cfg_from(body.get("config")))
                ROOMS[room.code] = room
                return self._send(200, room.meta(user))
            m = re.fullmatch(r"/rooms/([A-Za-z0-9]+)(/[a-z]+)?", path)
            if not m:
                return self._send(404, {"error": "không tìm thấy"})
            room = ROOMS.get(m.group(1).upper())
            if not room:
                return self._send(404, {"error": "Phòng không tồn tại"})
            sub, side = m.group(2) or "", room.side_of(user)
            if sub == "/view":
                return self._send(200, {"room": room.meta(user), "game": room.game.view()})
            if sub == "/agentlog":
                return self._send(200, {"log": list(room.agent_log.get(user, []))[-200:]})
            if method != "POST":
                return self._send(404, {"error": "không tìm thấy"})
            if sub == "/join":
                if side:
                    return self._send(200, room.meta(user))
                if room.players["top"] is not None:
                    return self._send(409, {"error": "Phòng đã đủ người"})
                room.players["top"], room.ready["top"], room.agent_seen["top"] = user, False, False
                return self._send(200, room.meta(user))
            if not side:
                return self._send(403, {"error": "Bạn không ở trong phòng này"})
            if sub == "/leave":
                if room.game.status == "live":
                    return self._send(409, {"error": "Đang đấu, không rời được"})
                room.players[side] = None
                room.ready[side] = room.agent_seen[side] = False
                if not any(room.players.values()) or room.players["bottom"] is None:
                    ROOMS.pop(room.code, None)
                return self._send(200, {"ok": True})
            if sub == "/ready":
                want = bool(body.get("ready", True))
                if want and not room.agent_seen[side]:
                    return self._send(409, {"error": "Agent chưa gọi API cho phòng này — gõ prompt có mã phòng cho agent trước"})
                if room.game.status != "scheduled":
                    return self._send(409, {"error": f"Trận đang ở trạng thái {room.status}"})
                room.ready[side] = want
                if all(room.ready.values()) and all(room.players.values()):
                    room.countdown_until = time.time() + 5
                elif not want:
                    room.countdown_until = None
                return self._send(200, room.meta(user))
            if sub == "/config":
                if user != room.owner or room.game.status != "scheduled" or room.countdown_until:
                    return self._send(409, {"error": "Chỉ chủ phòng, trước khi trận bắt đầu"})
                room.cfg = cfg_from(body, room.cfg)
                room.new_game()
                return self._send(200, room.meta(user))
            if sub == "/rematch":
                if user != room.owner or room.game.status != "finished":
                    return self._send(409, {"error": "Chỉ chủ phòng, sau khi trận kết thúc"})
                room.new_game()
                return self._send(200, room.meta(user))
            return self._send(404, {"error": "không tìm thấy"})


def main():
    global USERS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default=os.path.join(HERE, "data"), help="thư mục lưu tài khoản/token")
    a = ap.parse_args()
    USERS = Users(os.path.join(a.data, "users.json"))
    threading.Thread(target=tick_loop, daemon=True).start()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    host = "localhost" if a.host in ("127.0.0.1", "0.0.0.0") else a.host
    print(f"Battle City Online (luyện tập) — mở http://{host}:{a.port}")
    print(f"Agent: BCO_BASE_URL=http://{host}:{a.port}/api/v1")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
