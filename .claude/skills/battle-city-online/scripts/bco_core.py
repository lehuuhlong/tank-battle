"""Battle City Online — lõi: HTTP client, bản đồ/tìm đường/đường đạn, chuẩn hoá state. Chỉ dùng thư viện chuẩn."""
import heapq
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(SKILL_DIR, "references", "state-schema.json")

# Windows consoles default to cp1252/cp437: force UTF-8 so Vietnamese text and emoji print.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ======================================================================
# CLIENT
# ======================================================================
DEFAULT_BASE = "http://tank.recoff.cloud/api/v1"


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} {url}: {body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:500]}")


class Client:
    def __init__(self, token=None, base_url=None, model=None, timeout=10, retries=3):
        self.token = token or os.environ.get("BCO_TOKEN")
        if not self.token:
            raise SystemExit("BCO_TOKEN chưa được đặt (export BCO_TOKEN=...)")
        self.base = (base_url or os.environ.get("BCO_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("BCO_AGENT_MODEL")
        self.timeout = timeout
        self.retries = retries
        self.n429 = 0  # the runner watches this to slow its loop down

    # -- low level -------------------------------------------------------
    def request(self, method, path, body=None, params=None):
        url = self.base + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if self.model:
            headers["X-Agent-Model"] = self.model
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return _decode(r.read())
            except urllib.error.HTTPError as e:
                payload = _decode(e.read())
                if e.code == 429:  # rate limited: back off and retry
                    self.n429 += 1
                    last = ApiError(e.code, payload, url)
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    time.sleep(float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 1.0 * (attempt + 1))
                    continue
                # 4xx are answers (e.g. rejected orders): do not retry, surface them
                if 400 <= e.code < 500:
                    raise ApiError(e.code, payload, url)
                last = ApiError(e.code, payload, url)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
            time.sleep(0.3 * (attempt + 1))
        raise last

    # -- endpoints -------------------------------------------------------
    def matches(self):
        return self.request("GET", "/matches")

    def match(self, code):
        return self.request("GET", f"/matches/{code}")

    def rules(self, code):
        return self.request("GET", f"/matches/{code}/rules")

    def state(self, code, with_map=True):
        return self.request("GET", f"/matches/{code}/state", params={"map": "true"} if with_map else None)

    def plan(self, code, steps):
        return self.request("POST", f"/matches/{code}/plan", {"steps": steps})

    def order(self, code, step):
        return self.request("POST", f"/matches/{code}/orders", step)

    def stop(self, code, tank):
        return self.request("POST", f"/matches/{code}/stop", {"tank": tank})


def _decode(raw):
    text = raw.decode("utf-8", "replace") if raw else ""
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text


def find_rejections(response, sent_tanks):
    """Best-effort: return {tank_id: reason} for tanks the server rejected in a /plan response.

    The response format is not documented, so we look for any dict that names a tank and
    carries an error/reason/ok=false/accepted=false. Unknown formats -> {} (log the raw reply).
    """
    out = {}

    def visit(node):
        if isinstance(node, dict):
            tank = node.get("tank", node.get("tank_id", node.get("id")))
            failed = (
                node.get("ok") is False
                or node.get("accepted") is False
                or node.get("success") is False
                or str(node.get("status", "")).lower() in ("rejected", "error", "failed")
                or ("error" in node and node["error"])
            )
            if tank is not None and failed:
                out[_tank_key(tank)] = node.get("error") or node.get("reason") or node.get("message") or node
            for k, v in node.items():
                if k in ("rejected", "errors", "failed") and isinstance(v, dict):
                    for tk, reason in v.items():
                        out[_tank_key(tk)] = reason
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(response)
    return {k: v for k, v in out.items() if k in {_tank_key(t) for t in sent_tanks}} or out


def _tank_key(t):
    try:
        return int(t)
    except (TypeError, ValueError):
        return t


# ======================================================================
# GRID
# ======================================================================
DELTA = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
DIRS = ("up", "right", "down", "left")

PASSABLE = {"empty", "tree", "ice"}
BULLET_THROUGH = {"empty", "tree", "ice", "water", "eagle"}


class Grid:
    def __init__(self, rows, legend=None, unknown_as="empty"):
        legend = dict(legend or {".": "empty", "#": "brick", "@": "steel", "%": "tree", "~": "water", "-": "ice", " ": "empty"})
        for ch in OVERLAY_CHARS:  # tanks / eagles / bullets drawn on the map sit on passable ground
            legend.setdefault(ch, "empty")
        legend.setdefault("x", "steel")  # destroyed base
        # A power-up (P) is drawn over whatever is below it, often brick. Unknown ground = "item_cell":
        # treated like brick (approach with move_fire, counts as a brick for bullets) until the
        # runner fills it in from terrain remembered in earlier cycles.
        if legend.get("P") in (None, "empty"):
            legend["P"] = "item_cell"
        self.rows = list(rows)
        self.H, self.W = len(rows), max(len(r) for r in rows)
        self.unknown_chars = set()
        self.cells = []
        for r in rows:
            line = []
            for ch in r.ljust(self.W, "."):
                kind = legend.get(ch)
                if kind is None:
                    self.unknown_chars.add(ch)
                    kind = unknown_as
                line.append(kind)
            self.cells.append(line)

    def at(self, x, y):
        if 0 <= x < self.W and 0 <= y < self.H:
            return self.cells[y][x]
        return "wall"

    def mark_block(self, anchor, s, kind):
        x0, y0 = anchor
        for y in range(y0, y0 + s):
            for x in range(x0, x0 + s):
                if 0 <= x < self.W and 0 <= y < self.H:
                    self.cells[y][x] = kind

    def footprint(self, x, y, s):
        return [self.at(x + i, y + j) for j in range(s) for i in range(s)]

    def enter_cost(self, x, y, s, star3=False):
        """Cost of putting a tank's anchor at (x, y); None if impossible. Returns (cost, needs_fire)."""
        fire = False
        for k in self.footprint(x, y, s):
            if k in PASSABLE:
                continue
            if k in ("brick", "item_cell") or (k == "steel" and star3):
                fire = True
                continue
            return None
        return (4 if fire else 1), fire


def overlaps(ax, ay, bx, by, s):
    return abs(ax - bx) < s and abs(ay - by) < s


def dijkstra(grid, start, s, star3=False, penalties=None, max_cost=None):
    """Shortest paths over tank anchors. Returns dist {anchor: cost} and prev {anchor: parent}."""
    penalties = penalties or {}
    dist, prev = {start: 0}, {}
    pq = [(0, start)]
    while pq:
        c, cur = heapq.heappop(pq)
        if c > dist.get(cur, 1e9):
            continue
        if max_cost is not None and c > max_cost:
            break
        for d in DIRS:
            dx, dy = DELTA[d]
            nxt = (cur[0] + dx, cur[1] + dy)
            ec = grid.enter_cost(nxt[0], nxt[1], s, star3)
            if ec is None:
                continue
            nc = c + ec[0] + penalties.get(nxt, 0)
            if nc < dist.get(nxt, 1e9):
                dist[nxt], prev[nxt] = nc, cur
                heapq.heappush(pq, (nc, nxt))
    return dist, prev


def build_path(prev, start, goal):
    if goal != start and goal not in prev:
        return None
    path = [goal]
    while path[-1] != start:
        path.append(prev[path[-1]])
    return path[::-1]


def path_to_steps(grid, path, s, star3=False, max_steps=5):
    """Compress anchor path into move / move_fire commands (straight runs)."""
    steps = []
    for a, b in zip(path, path[1:]):
        d = next(k for k, v in DELTA.items() if v == (b[0] - a[0], b[1] - a[1]))
        fire = grid.enter_cost(b[0], b[1], s, star3)[1]
        do = "move_fire" if fire else "move"
        if steps and steps[-1]["direction"] == d and steps[-1]["do"] == do:
            steps[-1]["cells"] += 1
        else:
            if len(steps) >= max_steps:
                break
            steps.append({"do": do, "direction": d, "cells": 1})
    return steps


def direction_between(a, b, s):
    """If b's footprint is in a straight lane from a, return the direction to fire; else None."""
    ax, ay = a
    bx, by = b
    if abs(ax - bx) < s:
        return "up" if by < ay else "down" if by > ay else None
    if abs(ay - by) < s:
        return "left" if bx < ax else "right" if bx > ax else None
    return None


def trace(grid, origin, d, s, entities, star3=False, max_range=60):
    """Follow a bullet lane from a tank at `origin` in direction d.

    entities: list of (kind, anchor, obj). Returns dict(kind, obj, anchor, bricks, dist) for the
    first entity hit, or dict(kind='steel'/'wall', ...) when blocked. Bricks are counted (each
    roughly needs one extra shot), steel blocks unless star3.
    """
    x, y = origin
    dx, dy = DELTA[d]
    bricks = 0
    for step in range(1, max_range + 1):
        if dy:  # vertical lane: columns x..x+s-1, one row at a time
            r = y - step if dy < 0 else y + s - 1 + step
            cells = [(x + i, r) for i in range(s)]
        else:
            c = x - step if dx < 0 else x + s - 1 + step
            cells = [(c, y + j) for j in range(s)]
        # entities first (they sit on passable cells / eagle cells)
        for kind, (ex, ey), obj in entities:
            if any(ex <= cx < ex + s and ey <= cy < ey + s for cx, cy in cells):
                return {"kind": kind, "obj": obj, "anchor": (ex, ey), "bricks": bricks, "dist": step}
        kinds = [grid.at(cx, cy) for cx, cy in cells]
        if all(k == "wall" for k in kinds):
            return {"kind": "wall", "obj": None, "anchor": None, "bricks": bricks, "dist": step}
        if "steel" in kinds and not star3:
            return {"kind": "steel", "obj": None, "anchor": None, "bricks": bricks, "dist": step}
        if "brick" in kinds or "steel" in kinds or "item_cell" in kinds:
            bricks += 1
    return {"kind": "none", "obj": None, "anchor": None, "bricks": bricks, "dist": max_range}


def render(grid, overlays=None):
    """ASCII render with overlays {(x, y): char} (anchor cells)."""
    rows = [list(r) for r in grid.rows]
    for (x, y), ch in (overlays or {}).items():
        if 0 <= y < grid.H and 0 <= x < grid.W:
            rows[y][x] = ch
    return "\n".join("".join(r) for r in rows)


# ======================================================================
# STATE
# ======================================================================
DIRS = ("up", "right", "down", "left")
OPPOSITE = {"top": "bottom", "bottom": "top"}

# Candidate paths tried (in order) when the schema leaves a key null.
CANDIDATES = {
    "status": ["status", "state", "phase", "match.status", "match.state"],
    "time_left": ["time_left_s", "time_left", "timeLeft", "remaining", "clock", "time", "game_time_left", "match.time_left", "timer"],
    "armor_left": ["bases_open_in_s", "armor_left", "armorLeft", "armor", "shield_left", "base_armor", "armor_time_left", "match.armor_left"],
    "my_tanks": ["my_tanks", "myTanks", "you.tanks", "me.tanks", "self.tanks", "mine.tanks", "ally.tanks",
                 "allies", "tanks.mine", "tanks.my", "tanks.you", "tanks.{me}", "players.{me}.tanks", "sides.{me}.tanks", "{me}.tanks"],
    "enemy_tanks": ["enemy_tanks", "enemyTanks", "enemy.tanks", "opponent.tanks", "enemies", "foe.tanks",
                    "tanks.enemy", "tanks.opponent", "tanks.{enemy}", "players.{enemy}.tanks", "sides.{enemy}.tanks", "{enemy}.tanks"],
    "all_tanks": ["tanks", "units"],
    "my_base_hp": ["bases.yours.hp", "my_base_hp", "you.base_hp", "you.base.hp", "me.base.hp", "you.eagle_hp", "you.eagle.hp",
                   "bases.{me}.hp", "bases.{me}", "base_hp.{me}", "eagles.{me}.hp", "eagles.{me}", "{me}.base.hp", "{me}.base_hp"],
    "enemy_base_hp": ["bases.opponent.hp", "enemy_base_hp", "enemy.base_hp", "enemy.base.hp", "opponent.base.hp", "enemy.eagle_hp", "enemy.eagle.hp",
                      "bases.{enemy}.hp", "bases.{enemy}", "base_hp.{enemy}", "eagles.{enemy}.hp", "eagles.{enemy}", "{enemy}.base.hp", "{enemy}.base_hp"],
    "my_score": ["my_score", "you.score", "me.score", "score.{me}", "scores.{me}", "score.you", "score.me", "{me}.score"],
    "enemy_score": ["enemy_score", "enemy.score", "opponent.score", "score.{enemy}", "scores.{enemy}", "score.enemy", "score.opponent", "{enemy}.score"],
    "enemy_base_info": ["bases.opponent", "bases.{enemy}"],
    "my_base_info": ["bases.yours", "bases.{me}"],
    "events": ["events", "new_events", "log", "recent_events"],
    "items": ["powerups", "items", "power_ups", "bonuses", "pickups"],
    "map": ["map", "ascii", "ascii_map", "board", "grid", "map.ascii", "map.rows"],
    "my_side": ["my_side", "side", "you.side", "me.side", "you", "your_side"],
    "my_base_pos": ["bases.yours", "you.base", "you.eagle", "me.base", "my_base", "bases.{me}", "eagles.{me}", "base.{me}", "eagle.{me}", "{me}.base", "{me}.eagle"],
    "enemy_base_pos": ["bases.opponent", "enemy.base", "enemy.eagle", "opponent.base", "enemy_base", "bases.{enemy}", "eagles.{enemy}", "base.{enemy}", "eagle.{enemy}", "{enemy}.base", "{enemy}.eagle"],
}
LIVE_STATUSES = {"live", "playing", "running", "in_progress", "started", "active"}
WAIT_STATUSES = {"scheduled", "preparing", "waiting", "pending", "lobby", "ready", "countdown", "starting"}
TANK_FIELDS = {
    "id": ["id", "tank", "tank_id", "index", "n", "no"],
    "x": ["x", "col", "cx"], "y": ["y", "row", "cy"],
    "pos": ["pos", "position", "xy", "cell", "at"],
    "dir": ["dir", "direction", "facing", "heading"],
    "lives": ["lives", "life", "lifes", "hp"],
    "stars": ["stars", "star", "level", "rank"],
    "order": ["order", "orders", "plan", "queue", "current_order", "job", "task"],
    "shield": ["shield", "shielded", "invulnerable", "helmet", "spawn_shield"],
    "alive": ["alive", "is_alive"],
    "side": ["side", "owner", "team", "player"],
    "mine": ["mine", "is_mine", "own", "ally", "friendly"],
}
ITEM_FIELDS = {"kind": ["kind", "type", "name", "item"], "x": ["x", "col"], "y": ["y", "row"], "pos": ["pos", "position", "xy", "at"]}
EVENT_FIELDS = {"type": ["type", "kind", "event", "name"], "tank": ["tank", "tank_id", "unit"]}


def load_schema(path=SCHEMA_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ---------------------------------------------------------------- path helpers
def get_path(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            if not -len(cur) <= i < len(cur):
                return None
            cur = cur[i]
        else:
            return None
    return cur


def resolve(obj, key, schema, me=None, found=None):
    """Return (value, path) for a logical key, trying the schema first then candidates."""
    spec = schema.get(key)
    paths = [spec] if isinstance(spec, str) else list(spec or []) or CANDIDATES.get(key, [])
    for p in paths:
        if "{me}" in p or "{enemy}" in p:
            if not me:
                continue
            p = p.replace("{me}", me).replace("{enemy}", OPPOSITE.get(me, ""))
        v = get_path(obj, p)
        if v is not None:
            if found is not None:
                found[key] = p
            return v, p
    return None, None


def pick(d, names, override=None):
    if not isinstance(d, dict):
        return None
    for n in ([override] if override else []) + names:
        if n and n in d and d[n] is not None:
            return d[n]
    return None


# ---------------------------------------------------------------- value parsers
def parse_seconds(v):
    """'5:32' -> 332, '75s' -> 75, 75 -> 75, {'seconds':..} -> .."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        return parse_seconds(pick(v, ["seconds", "left", "remaining", "value", "game"]))
    s = str(v).strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?:\.\d+)?", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*s?", s)
    return float(m.group(1)) if m else None


def parse_pos(d, fmap):
    x, y = pick(d, TANK_FIELDS["x"] if "x" not in fmap else [], fmap.get("x")), pick(d, TANK_FIELDS["y"] if "y" not in fmap else [], fmap.get("y"))
    if x is None or y is None:
        p = pick(d, TANK_FIELDS["pos"], fmap.get("pos"))
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            x, y = p[0], p[1]
        elif isinstance(p, dict):
            x, y = p.get("x"), p.get("y")
        elif isinstance(p, str) and re.fullmatch(r"\s*\d+\s*[,: ]\s*\d+\s*", p):
            x, y = map(int, re.split(r"[,: ]+", p.strip()))
    try:
        return int(round(float(x))), int(round(float(y)))
    except (TypeError, ValueError):
        return None


def parse_dir(v):
    if v is None:
        return None
    if isinstance(v, int):
        return DIRS[v % 4]
    s = str(v).lower()
    for d, alts in {"up": ("up", "u", "n", "north", "^"), "right": ("right", "r", "e", "east", ">"),
                    "down": ("down", "d", "s", "south", "v"), "left": ("left", "l", "w", "west", "<")}.items():
        if s in alts:
            return d
    return None


def parse_side(v):
    if v is None:
        return None
    s = str(v).lower()
    if any(k in s for k in ("bottom", "lower", "south", "dưới", "duoi")):
        return "bottom"
    if any(k in s for k in ("top", "upper", "north", "trên", "tren")):
        return "top"
    return None


# ---------------------------------------------------------------- model
@dataclass
class Tank:
    id: object
    x: int
    y: int
    dir: str = None
    lives: int = None
    stars: int = 0
    order: object = None
    shield: bool = False
    alive: bool = True
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def pos(self):
        return (self.x, self.y)

    @property
    def busy(self):
        """True/False if the state tells us whether the tank still has queued orders, None if unknown."""
        if "order" not in self._known:
            return None
        o = self.order
        if o in (None, "", [], {}, False):
            nxt = self.raw.get("next") if isinstance(self.raw, dict) else None
            return bool(nxt)  # real server: "order" = current step, "next" = queued steps
        if isinstance(o, str) and o.lower() in ("idle", "none", "null"):
            return False
        return True

    _known: set = field(default_factory=set, repr=False)


@dataclass
class Item:
    kind: str
    x: int
    y: int


@dataclass
class Game:
    my_side: str
    grid: Grid
    my_tanks: list
    enemy_tanks: list
    status: str = None
    time_left: float = None
    armor_left: float = None
    armor_active: bool = False
    my_base_hp: int = None
    enemy_base_hp: int = None
    my_score: int = None
    enemy_score: int = None
    events: list = field(default_factory=list)
    round: int = None                # command round counter (real server: "round")
    next_round_in_s: float = None    # real seconds until the next command round
    bullets: list = field(default_factory=list)  # [{"x", "y", "dir", "mine"}] bullets in flight
    items: list = field(default_factory=list)
    my_base: tuple = None
    enemy_base: tuple = None
    enemy_base_open: bool = None     # from bases.opponent.invulnerable (None = unknown → use timers)
    my_base_open: bool = None
    tank_size: int = 2
    enemy_half_bound: dict = field(default_factory=dict)
    schema: dict = field(default_factory=dict, repr=False)
    found: dict = field(default_factory=dict, repr=False)
    warnings: list = field(default_factory=list)
    now: float = field(default_factory=time.monotonic)
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def enemy_side(self):
        return OPPOSITE[self.my_side]

    def in_enemy_half(self, y):
        b = self.enemy_half_bound
        return y <= b.get("bottom_max_y", 19) if self.my_side == "bottom" else y >= b.get("top_min_y", 21)

    def in_my_half(self, y):
        b = self.enemy_half_bound
        return y >= b.get("top_min_y", 21) if self.my_side == "bottom" else y <= b.get("bottom_max_y", 19)

    @property
    def live(self):
        st = str(self.status or "").lower()
        return not st or st in LIVE_STATUSES or st not in WAIT_STATUSES and not self.finished

    @property
    def waiting(self):
        return str(self.status or "").lower() in WAIT_STATUSES

    @property
    def finished(self):
        return str(self.status or "").lower() in ("finished", "ended", "over", "done", "complete", "completed")

    def alive_tanks(self, mine=True):
        return [t for t in (self.my_tanks if mine else self.enemy_tanks) if t.alive]

    def tank(self, tid):
        for t in self.my_tanks:
            if t.id == tid:
                return t
        return None


# ---------------------------------------------------------------- map extraction
def find_map_rows(obj, schema=None, found=None):
    """Locate the ASCII map: a multi-line string or a list of equal-length strings (>=5 rows)."""
    schema = schema or {}
    if schema.get("map"):
        v, _ = resolve(obj, "map", schema, found=found)
        rows = _as_rows(v)
        if rows:
            return rows
    best = None

    def visit(node, path):
        nonlocal best
        rows = _as_rows(node)
        if rows and (best is None or len(rows) * len(rows[0]) > len(best[0]) * len(best[0][0])):
            best = (rows, path)
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, f"{path}.{k}" if path else str(k))
        elif isinstance(node, list) and not (node and all(isinstance(x, str) for x in node)):
            for i, v in enumerate(node):
                visit(v, f"{path}.{i}" if path else str(i))

    visit(obj, "")
    if best:
        if found is not None:
            found["map"] = best[1] or "<root>"
        return best[0]
    return None


def _as_rows(v):
    if isinstance(v, str) and "\n" in v:
        rows = [r for r in v.split("\n") if r.strip("\r")]
        rows = [r.rstrip("\r") for r in rows]
    elif isinstance(v, list) and v and all(isinstance(r, str) for r in v):
        rows = list(v)
    else:
        return None
    if len(rows) < 5:
        return None
    width = max(len(r) for r in rows)
    if width < 5 or sum(len(r) == width for r in rows) < len(rows) * 0.8:
        return None
    return [r.ljust(width, ".") for r in rows]


# ---------------------------------------------------------------- side detection
def detect_side(match_info):
    """Find 'top'/'bottom' anywhere plausible in GET /matches/{code}."""
    if not isinstance(match_info, (dict, list)):
        return parse_side(match_info)
    preferred = ("side", "your_side", "my_side", "you", "position", "seat", "role", "half")
    hits = []

    def visit(node, key):
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, str(k).lower())
        elif isinstance(node, list):
            for v in node:
                visit(v, key)
        else:
            s = parse_side(node)
            if s:
                hits.append((0 if key in preferred else 1, s))

    visit(match_info, "")
    hits.sort(key=lambda h: h[0])
    return hits[0][1] if hits else None


# ---------------------------------------------------------------- main entry
OVERLAY_CHARS = "123456789OHTPNx*"


def map_entities(rows, s=2):
    """Read what the ASCII map draws on top of terrain (player-relative):
    digits = our tanks (digit = tank number), O = enemy tank, H = our eagle (home), T = enemy eagle
    (target), P = power-up. Returns anchors (top-left cell of each 2x2 block)."""
    seen = set()
    out = {"mine": [], "enemy": [], "my_base": None, "enemy_base": None, "items": []}
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch not in OVERLAY_CHARS or (x, y) in seen:
                continue
            for j in range(s):
                for i in range(s):
                    if y + j < len(rows) and x + i < len(rows[y + j]) and rows[y + j][x + i] == ch:
                        seen.add((x + i, y + j))
            if ch.isdigit():
                out["mine"].append((int(ch), x, y))
            elif ch in "ON":  # O = opponent tank, N = NPC tank (hostile)
                out["enemy"].append((x, y))
            elif ch == "H":
                out["my_base"] = out["my_base"] or (x, y)
            elif ch == "T":
                out["enemy_base"] = out["enemy_base"] or (x, y)
            elif ch == "P":
                out["items"].append((x, y))
    return out


def parse_bullets(lst, my_side):
    """bullets[] from the state: keep position, direction and owner when the server gives them."""
    out = []
    for b in lst or []:
        if not isinstance(b, dict) or b.get("x") is None or b.get("y") is None:
            continue
        d = parse_dir(pick(b, ["facing", "dir", "direction", "heading"]))
        owner = pick(b, ["owner", "side", "team", "from", "by", "mine"])
        mine = None
        if isinstance(owner, bool):
            mine = owner
        elif owner is not None:
            o = str(owner).lower()
            mine = True if o in ("you", "yours", "mine", "me", "ally", my_side) else \
                False if o in ("opponent", "enemy", "them", "npc", OPPOSITE.get(my_side, "")) else None
        try:
            out.append({"x": float(b["x"]), "y": float(b["y"]), "dir": d, "mine": mine})
        except (TypeError, ValueError):
            pass
    return out


def normalize(state, my_side=None, schema=None):
    schema = schema if schema is not None else load_schema()
    found, warnings = {}, []
    if not my_side:
        side_v, _ = resolve(state, "my_side", schema, found=found)
        my_side = parse_side(side_v if not isinstance(side_v, dict) else side_v.get("side"))
    rows = find_map_rows(state, schema, found)
    s = int(schema.get("tank_size", 2))
    ents = map_entities(rows, s) if rows else None
    if my_side not in ("top", "bottom") and ents and ents["my_base"]:
        my_side = "bottom" if ents["my_base"][1] >= len(rows) // 2 else "top"  # H = our eagle
    if my_side not in ("top", "bottom"):
        raise ValueError("Không xác định được bên (top/bottom). Truyền --side hoặc điền my_side trong state-schema.json")

    if not rows:
        warnings.append("Không thấy bản đồ ASCII trong state (gọi state?map=true).")
        rows = ["." * 42] * 42
    grid = Grid(rows, schema.get("legend"), schema.get("unknown_char_as", "empty"))
    if grid.unknown_chars:
        warnings.append(f"Ký tự bản đồ chưa có trong legend: {sorted(grid.unknown_chars)} (đang coi là {schema.get('unknown_char_as', 'empty')})")

    tf = {k: v for k, v in (schema.get("tank_fields") or {}).items() if v}

    def parse_tanks(lst):
        out = []
        for i, d in enumerate(lst or []):
            if not isinstance(d, dict):
                continue
            pos = parse_pos(d, tf)
            if pos is None:
                continue
            t = Tank(id=pick(d, TANK_FIELDS["id"], tf.get("id")) or (i + 1), x=pos[0], y=pos[1], raw=d)
            t.dir = parse_dir(pick(d, TANK_FIELDS["dir"], tf.get("dir")))
            t.lives = pick(d, TANK_FIELDS["lives"], tf.get("lives"))
            st = pick(d, TANK_FIELDS["stars"], tf.get("stars"))
            t.stars = int(st) if isinstance(st, (int, float)) else 0
            okey = next((k for k in ([tf.get("order")] if tf.get("order") else []) + TANK_FIELDS["order"] if k in d), None)
            if okey:
                t.order = d[okey]
                t._known.add("order")
            sh = pick(d, TANK_FIELDS["shield"], tf.get("shield"))
            t.shield = bool(sh) and sh not in (0, "0", "false")
            al = pick(d, TANK_FIELDS["alive"], tf.get("alive"))
            t.alive = (al is not False) and (t.lives is None or not isinstance(t.lives, (int, float)) or t.lives > 0 or al is True)
            if isinstance(t.id, str) and t.id.isdigit():
                t.id = int(t.id)
            out.append(t)
        return out

    my_raw, _ = resolve(state, "my_tanks", schema, my_side, found)
    en_raw, _ = resolve(state, "enemy_tanks", schema, my_side, found)
    if my_raw is None or en_raw is None:
        all_raw, _ = resolve(state, "all_tanks", schema, my_side, found)
        if isinstance(all_raw, dict):  # {"top":[...],"bottom":[...]} or {"mine":[...],"enemy":[...]}
            my_raw = my_raw or next((all_raw[k] for k in all_raw if parse_side(k) == my_side or k in ("mine", "my", "you", "me", "ally")), None)
            en_raw = en_raw or next((all_raw[k] for k in all_raw if parse_side(k) == OPPOSITE[my_side] or k in ("enemy", "opponent", "them", "foe")), None)
        elif isinstance(all_raw, list):
            mine, theirs = [], []
            for d in all_raw:
                if not isinstance(d, dict):
                    continue
                flag = pick(d, TANK_FIELDS["mine"], tf.get("mine"))
                side = parse_side(pick(d, TANK_FIELDS["side"], tf.get("side")))
                if flag is not None:
                    (mine if flag else theirs).append(d)
                elif side:
                    (mine if side == my_side else theirs).append(d)
            if mine or theirs:
                my_raw, en_raw = mine, theirs
                found.setdefault("my_tanks", "tanks[mine/side]")
    my_tanks, enemy_tanks = parse_tanks(my_raw if isinstance(my_raw, list) else []), parse_tanks(en_raw if isinstance(en_raw, list) else [])
    if not my_tanks and ents and ents["mine"]:
        my_tanks = [Tank(id=i, x=x, y=y) for i, x, y in sorted(ents["mine"])]
        found.setdefault("my_tanks", "map[1-4]")
    if not enemy_tanks and ents and ents["enemy"]:
        enemy_tanks = [Tank(id=k + 1, x=x, y=y) for k, (x, y) in enumerate(ents["enemy"])]
        found.setdefault("enemy_tanks", "map[O]")
    if not my_tanks:
        warnings.append("Không tìm thấy xe của ta — điền my_tanks/tank_fields trong state-schema.json.")

    def num(key):
        v, _ = resolve(state, key, schema, my_side, found)
        if isinstance(v, dict):
            v = pick(v, ["hp", "value", "health", "lives", "score"])
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    status, _ = resolve(state, "status", schema, my_side, found)
    tl_raw, _ = resolve(state, "time_left", schema, my_side, found)
    ar_raw, _ = resolve(state, "armor_left", schema, my_side, found)
    time_left, armor_left = parse_seconds(tl_raw), parse_seconds(ar_raw)
    if isinstance(ar_raw, bool):
        armor_active = ar_raw
    elif armor_left is not None:
        armor_active = armor_left > 0
    elif time_left is not None:
        elapsed = schema.get("game_seconds_total", 480) - time_left
        armor_active = elapsed < schema.get("armor_game_seconds", 180)
    else:
        armor_active = False
        warnings.append("Không biết thời gian/giáp — coi như hết giáp.")

    items = []
    items_raw, _ = resolve(state, "items", schema, my_side, found)
    ifm = {k: v for k, v in (schema.get("item_fields") or {}).items() if v}
    for d in items_raw or []:
        if isinstance(d, dict):
            pos = parse_pos(d, ifm)
            if pos:
                items.append(Item(kind=str(pick(d, ITEM_FIELDS["kind"], ifm.get("kind")) or "?").lower(), x=pos[0], y=pos[1]))

    if not items and ents:
        items = [Item(kind="?", x=x, y=y) for x, y in ents["items"]]

    events, _ = resolve(state, "events", schema, my_side, found)

    # eagle positions: state first (30 maps exist), then schema base_pos, then bottom/top centre
    bp = schema.get("base_pos") or {}
    W, H = grid.W, grid.H
    default_base = {"top": (W // 2 - s // 2, 0), "bottom": (W // 2 - s // 2, H - s)}

    def base_from_state(key):
        v, _ = resolve(state, key, schema, my_side, found)
        return parse_pos(v, {}) if isinstance(v, dict) else (tuple(v[:2]) if isinstance(v, list) and len(v) >= 2 and all(isinstance(c, int) for c in v[:2]) else None)

    my_base = (base_from_state("my_base_pos") or (ents or {}).get("my_base")
               or tuple(bp.get(my_side) or default_base[my_side]))
    enemy_base = (base_from_state("enemy_base_pos") or (ents or {}).get("enemy_base")
                  or tuple(bp.get(OPPOSITE[my_side]) or default_base[OPPOSITE[my_side]]))
    grid.mark_block(my_base, s, "eagle")
    grid.mark_block(enemy_base, s, "eagle")
    # armor (first 3:00) and the few seconds after each hit: the wall ring around the eagle is
    # steel (only 3-star tanks break it). The ASCII map probably shows it already; make sure.
    if armor_active:
        for bx, by in (my_base, enemy_base):
            for y in range(by - 1, by + s + 1):
                for x in range(bx - 1, bx + s + 1):
                    if grid.at(x, y) == "brick":
                        grid.cells[y][x] = "steel"


    def base_open(key):
        v, _ = resolve(state, key, schema, my_side, found)
        if not isinstance(v, dict):
            return None
        if v.get("alive") is False:
            return False
        inv = v.get("invulnerable")
        return None if inv is None else not bool(inv)

    st_val = status if not isinstance(status, dict) else pick(status, ["status", "state", "phase"])
    return Game(
        my_side=my_side, grid=grid, my_tanks=my_tanks, enemy_tanks=enemy_tanks,
        status=str(st_val) if st_val is not None else None,
        time_left=time_left, armor_left=armor_left, armor_active=armor_active,
        my_base_hp=num("my_base_hp"), enemy_base_hp=num("enemy_base_hp"),
        my_score=num("my_score"), enemy_score=num("enemy_score"),
        events=events if isinstance(events, list) else [], items=items,
        round=state.get("round") if isinstance(state.get("round"), int) else None,
        next_round_in_s=parse_seconds(state.get("next_round_in_s")),
        bullets=parse_bullets(state.get("bullets"), my_side),
        my_base=my_base, enemy_base=enemy_base, tank_size=s,
        enemy_base_open=base_open("enemy_base_info"), my_base_open=base_open("my_base_info"),
        enemy_half_bound=schema.get("enemy_half") or {"bottom_max_y": (H - s) // 2 - 1, "top_min_y": (H - s) // 2 + 1},
        schema=schema, found=found, warnings=warnings, raw=state,
    )


def event_type(ev, schema=None):
    ef = (schema or {}).get("event_fields") or {}
    if isinstance(ev, dict):
        return str(pick(ev, EVENT_FIELDS["type"], ef.get("type")) or "").lower()
    return str(ev).lower()


def event_tank(ev, schema=None):
    ef = (schema or {}).get("event_fields") or {}
    if isinstance(ev, dict):
        t = pick(ev, EVENT_FIELDS["tank"], ef.get("tank"))
        if isinstance(t, dict):
            t = pick(t, TANK_FIELDS["id"])
        try:
            return int(t)
        except (TypeError, ValueError):
            return t
    return None
