"""Battle City Online — engine mô phỏng (bản tự dựng để luyện tập, KHÔNG phải engine chính thức).

Luật theo trang hướng dẫn của game: xe 2x2 (toạ độ = ô trên-trái), nhịp lệnh 0,5s (lệnh hai bên áp dụng cùng
lúc ở đầu nhịp sau), tối đa 5 bước chờ, giáp 3:00 đồng hồ game (tường quanh đại bàng là thép), phải ở nửa sân
địch mới phá được nhà, trúng đại bàng → xe bị đưa về nhà + đại bàng bất tử 5s, vật phẩm theo cặp đối xứng...
Các con số chưa có trong tài liệu (tốc độ xe/đạn, thời gian chờ khi bị xe chặn...) nằm trong Config.
"""
import random
import time
from collections import deque
from dataclasses import dataclass, field, asdict

S = 2  # tank / eagle size in cells
DIRS = {"up": (0, -1), "right": (1, 0), "down": (0, 1), "left": (-1, 0)}
OPP = {"top": "bottom", "bottom": "top"}
ITEM_KINDS = ["star", "star", "star", "helmet", "shovel", "tank"]
ITEM_ICON = {"star": "⭐", "helmet": "⛑️", "shovel": "⛏️", "tank": "🛡️"}
SIDE_VI = {"bottom": "vàng", "top": "xanh"}


@dataclass
class Config:
    mode: str = "duel"
    tanks: int = 4
    lives: int = 15
    base_hp: int = 5
    game_seconds: int = 480          # đồng hồ game 8:00
    armor_game_seconds: int = 180    # giáp 3:00 đầu
    clock_rate: float = 0.5          # đồng hồ game chạy x0,5 → 8:00 = 16 phút thực
    tick_s: float = 0.5              # nhịp lệnh
    max_queue: int = 5
    score_target: int = 20000
    respawn_s: float = 3.0
    spawn_shield_s: float = 3.0
    eagle_invuln_s: float = 5.0
    item_every_s: float = 20.0
    item_ttl_s: float = 15.0
    helmet_s: float = 10.0
    shovel_s: float = 20.0
    tank_cells_per_s: float = 3.0    # "xe địch đi gần 3 ô mỗi giây"
    bullet_cells_per_s: float = 9.0
    bullet_fast_cells_per_s: float = 13.5  # 1 sao: đạn nhanh hơn
    blocked_wait_s: float = 1.0      # bị xe khác chắn quá lâu → lệnh move thất bại
    time_scale: float = 1.0          # tua nhanh để test (2 = mọi thứ nhanh gấp đôi)
    map: str = "random"

    def rules(self):
        return asdict(self)


# ----------------------------------------------------------------------------- maps
MAPS = {
    "san-1": """\
...................#..#...................
...................#..#...................
##.................####.........####..####
##.............#................####..####
##@@..%%%%%%@@##....##%%%%@@..........####
##@@..%%%%%%@@##....##%%%%@@..........####
@@@@%%%%%%%%@@##..####%%%%@@....@@..@@@@##
@@@@%%%%%%%%@@##..####%%%%@@....@@..@@@@##
##%%%%%%%%%%####......%%%%##....##........
##%%%%%%%%%%####......%%%%##....##........
##%%@@%%..@@..##..@@@@@@..@@......@@@@....
##%%@@%%..@@..##..@@@@@@##@@......@@@@....
@@..@@..@@@@..####..~~~~~~~~########......
@@..@@..@@@@..####..~~~~~~~~########......
............~~~~@@@@..########@@####......
............~~~~@@@@..########@@####......
......@@..............@@..................
......@@..............@@..................
@@....@@..##........@@@@..........@@@@##..
@@....@@..##........@@@@..........@@@@##..
.......................................#..
.......................................#..
..##@@@@..........@@@@......##..##@@....@@
..##@@@@..........@@@@......##..##@@....@@
..................@@..............@@......
..................@@..............@@......
......####@@.......#..@@@@~~~~............
......####@@.....#.#..@@@@~~~~............
......######..~~~~~~~~..####..@@@@..@@..@@
......####....~~~~~~~~....##..@@@@..@@..@@
....@@@@......@@##@@@@@@..##..@@..%%@@%%##
....@@@@......@@##@@@@@@..##..@@..%%@@%%##
........##......%%%%..........%%%%%%%%%%##
........##......%%%%..........%%%%%%%%%%##
##@@@@..@@....@@%%%%####...#@@%%%%%%%%@@@@
##@@@@..@@....@@%%%%.......#@@%%%%%%%%@@@@
####..........@@%%%%.......#@@%%%%%%..@@##
####..........@@%%%%.......#@@%%%%%%..@@##
####..####...............................#
####..####.........####..................#
...................#..#...................
...................#..#...................""",
    "san-2": """\
......####.........#..#...##....########..
......##...........#..#....#....########..
...................####...................
..........................................
............####..........................
............####..###.....................
@@..##@@##..@@..@@@@#...@@....@@@@##@@..@@
@@..##@@##..@@..@@@@##..@@....@@@@##@@..@@
........##..................%%%%####..####
........##..................%%%%####..####
@@....@@##......##@@@@@@@@..%%@@####@@####
@@....@@##......##@@@@@@@@..%%@@####@@####
@@......@@......@@##..######%%%%########@@
@@......@@......@@##..######%%%%########@@
@@..........@@~~~~@@~~..@@....@@....%%%%..
@@..........@@~~~~@@~~..@@....@@....%%%%..
....####..%%~~~~~~~~~~..............%%%%..
....####..%%~~~~~~~~~~..............%%%%..
@@..##@@@@%%@@####@@....@@##..@@....@@%%..
@@..##@@@@%%@@####@@....@@##..@@....@@%%..
..%%%%......%%######..######%%%%....%%%%..
..%%%%......%%######..######%%%%....%%%%..
..%%@@....@@..##@@....@@####@@%%@@@@##..@@
..%%@@....@@..##@@....@@####@@%%@@@@##..@@
..%%%%..............~~~~~~~~~~%%..####....
..%%%%..............~~~~~~~~~~%%..####....
..%%%%....@@....@@..~~@@~~~~@@..........@@
..%%%%....@@....@@..~~@@~~~~@@..........@@
@@########%%%%######..##@@.....#@@......@@
@@########%%%%######..##@@.....#@@......@@
####@@####@@%%..@@@@@@@@##......##@@....@@
####@@####@@%%..@@@@@@@@##......##@@....@@
####..####%%%%....######........##........
####..####%%%%....######........##........
@@..@@##@@@@....@@..##@@@@..@@..##@@##..@@
@@..@@##@@@@....@@..##@@@@..@@..##@@##..@@
..............##..######..####............
..............##..######..####............
..........................................
...................####...................
..########....##...#..#.......####........
..########....##...#..#.......####........""",
}
EAGLE = {"top": (20, 0), "bottom": (20, 40)}
SPAWNS = {"bottom": [(16, 40), (24, 40), (10, 40), (30, 40)], "top": [(24, 0), (16, 0), (30, 0), (10, 0)]}


def rotate180(x, y, w=42, h=42):
    return w - S - x, h - S - y


def generate_map(seed=None, w=42, h=42):
    """Random map, 180°-symmetric, built from 2x2 blocks, with walls around both eagles and a
    guaranteed path between the two bases."""
    rng = random.Random(seed)
    for _ in range(200):
        g = [["." for _ in range(w)] for _ in range(h)]
        for by in range(0, h // 2, 2):
            for bx in range(0, w, 2):
                ch = rng.choices(".#@~%", weights=[52, 24, 10, 5, 9])[0]
                for yy in (by, by + 1):
                    for xx in (bx, bx + 1):
                        g[yy][xx] = ch
                        g[h - 1 - yy][w - 1 - xx] = ch
        for side in ("top", "bottom"):
            for sx, sy in SPAWNS[side]:
                for yy in range(sy - (2 if side == "bottom" else 0), sy + S + (2 if side == "top" else 0)):
                    for xx in range(sx, sx + S):
                        if 0 <= yy < h:
                            g[yy][xx] = "."
            ex, ey = EAGLE[side]
            for yy in range(ey - 1, ey + S + 1):
                for xx in range(ex - 1, ex + S + 1):
                    if 0 <= yy < h:
                        g[yy][xx] = "." if ex <= xx < ex + S and ey <= yy < ey + S else "#"
        rows = ["".join(r) for r in g]
        if _connected(rows):
            return rows
    raise RuntimeError("không tạo được bản đồ")


def _connected(rows):
    grid = Grid(rows)
    start, goal = SPAWNS["bottom"][0], SPAWNS["top"][0]
    seen, q = {start}, deque([start])
    while q:
        x, y = q.popleft()
        if (x, y) == goal:
            return True
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n not in seen and grid.fits(*n, allow_brick=True):
                seen.add(n)
                q.append(n)
    return False


def load_map(name, seed=None):
    if name == "random":
        name = random.choice(list(MAPS) + ["gen"] * 3)
    if name in MAPS:
        return name, MAPS[name].split("\n")
    s = seed if seed is not None else random.randrange(10 ** 6)
    return f"gen-{s}", generate_map(s)


# ----------------------------------------------------------------------------- terrain
class Grid:
    def __init__(self, rows):
        self.h, self.w = len(rows), len(rows[0])
        self.c = [list(r) for r in rows]

    def at(self, x, y):
        return self.c[y][x] if 0 <= x < self.w and 0 <= y < self.h else "X"

    def fits(self, x, y, allow_brick=False):
        ok = ".%-#" if allow_brick else ".%-"
        return all(self.at(x + i, y + j) in ok for j in range(S) for i in range(S))

    def rows(self):
        return ["".join(r) for r in self.c]


def overlap(ax, ay, bx, by, sa=S, sb=S):
    return ax < bx + sb and bx < ax + sa and ay < by + sb and by < ay + sa


# ----------------------------------------------------------------------------- entities
@dataclass
class Tank:
    side: str
    id: int
    x: int
    y: int
    dir: str
    lives: int
    stars: int = 0
    plan: list = field(default_factory=list)
    alive: bool = True
    dead_until: float = 0.0
    shield_until: float = 0.0
    move_progress: float = 0.0       # 0..1 toward (x+dx, y+dy) while moving
    moving_to: tuple = None
    blocked_for: float = 0.0
    fire_cooldown: float = 0.0
    fired_in_step: int = 0
    doing: str = "đứng chờ"
    kills: int = 0
    deaths: int = 0
    items: int = 0
    eagle_hits: int = 0
    own_eagle_hits: int = 0
    idle_s: float = 0.0
    enemy_half_s: float = 0.0

    @property
    def out(self):
        return self.lives <= 0 and not self.alive


@dataclass
class Bullet:
    side: str
    tank_id: int
    x: float                     # lane origin (top-left of the 2-wide lane)
    y: float
    dir: str
    speed: float
    pierce_steel: bool
    shooter_y: int
    shooter_x: int = 0
    doing: str = ""
    dead: bool = False


@dataclass
class Item:
    kind: str
    x: int
    y: int
    until: float
    id: int = 0


# ----------------------------------------------------------------------------- game
class Game:
    """One match. All times are in *simulated real seconds* (t); the game clock = t * clock_rate."""

    def __init__(self, cfg: Config, map_name=None, seed=None):
        self.cfg = cfg
        self.rng = random.Random(seed)
        self.map_name, rows = load_map(map_name or cfg.map, seed)
        self.grid = Grid(rows)
        self.t = 0.0
        self.status = "scheduled"
        self.winner = None
        self.reason = ""
        self.reason_code = None
        self.score = {"top": 0, "bottom": 0}
        self.score_reached_at = {"top": 0.0, "bottom": 0.0}
        self.base_hp = {"top": cfg.base_hp, "bottom": cfg.base_hp}
        self.eagle_invuln_until = {"top": 0.0, "bottom": 0.0}
        self.shovel_until = {"top": 0.0, "bottom": 0.0}
        self.armor_over = False
        self.tanks = {s: [Tank(s, i + 1, *SPAWNS[s][i], "down" if s == "top" else "up", cfg.lives,
                                shield_until=cfg.spawn_shield_s) for i in range(cfg.tanks)] for s in ("top", "bottom")}
        self.bullets = []
        self.items = []
        self.next_item_at = cfg.item_every_s
        self.next_tick_at = 0.0
        self.pending = {"top": [], "bottom": []}   # orders waiting for the next command tick
        self.events = []                            # (seq, t, side_or_None, dict)
        self.seq = 0
        self.item_seq = 0
        self.round = 0
        self.friendly_fire = []                     # own-eagle hits, for diagnosis
        self.ring = {s: self._ring_cells(s) for s in ("top", "bottom")}
        self._set_ring("top", "@")
        self._set_ring("bottom", "@")

    # ------------------------------------------------------------ helpers
    @property
    def clock_elapsed(self):
        return self.t * self.cfg.clock_rate

    @property
    def time_left(self):
        return max(0.0, self.cfg.game_seconds - self.clock_elapsed)

    @property
    def armor_left(self):
        return max(0.0, self.cfg.armor_game_seconds - self.clock_elapsed)

    def _ring_cells(self, side):
        ex, ey = EAGLE[side]
        out = []
        for y in range(ey - 1, ey + S + 1):
            for x in range(ex - 1, ex + S + 1):
                if 0 <= x < self.grid.w and 0 <= y < self.grid.h and not (ex <= x < ex + S and ey <= y < ey + S):
                    if self.grid.at(x, y) in "#@":
                        out.append((x, y))
        return out

    def _set_ring(self, side, ch, only_existing=False):
        for x, y in self.ring[side]:
            if only_existing and self.grid.at(x, y) not in "#@":
                continue
            self.grid.c[y][x] = ch

    def in_half_of(self, side, y):
        """Is anchor row y inside `side`'s half? (top half: y ≤ 19; bottom half: y ≥ 21 on a 42 map)."""
        mid = (self.grid.h - S) // 2
        return y <= mid - 1 if side == "top" else y >= mid + 1

    def emit(self, ev, to=None):
        self.seq += 1
        ev = dict(ev, t=round(self.clock_elapsed, 1))
        self.events.append((self.seq, self.t, to, ev))
        if len(self.events) > 3000:
            self.events = self.events[-2000:]

    def all_tanks(self):
        return self.tanks["top"] + self.tanks["bottom"]

    def tank(self, side, tid):
        for t in self.tanks[side]:
            if t.id == tid:
                return t
        return None

    def name(self, t):
        return f"Xe {t.id} {SIDE_VI[t.side]}"

    # ------------------------------------------------------------ orders
    ALLOWED_KEYS = {"move": {"tank", "do", "direction", "cells"}, "move_fire": {"tank", "do", "direction", "cells"},
                    "turn": {"tank", "do", "direction"}, "fire": {"tank", "do", "count", "direction"}, "stop": {"tank", "do"}}

    def check_step(self, st):
        """Validate one step like the real server. Returns an error message or None."""
        if not isinstance(st, dict):
            return "each step must be an object"
        if st.get("tank") is None:
            return f"tank is required (1-{self.cfg.tanks})"
        do = st.get("do")
        if do not in self.ALLOWED_KEYS:
            return f"unknown step '{do}'"
        extra = sorted(set(st) - self.ALLOWED_KEYS[do])
        if extra:
            return f"{do} does not take {extra[0]}"
        if do in ("move", "move_fire", "turn") and st.get("direction") not in DIRS:
            return f"{do} needs direction up/right/down/left"
        if do == "fire" and st.get("direction") is not None and st.get("direction") not in DIRS:
            return "direction must be up/right/down/left"
        if do in ("move", "move_fire") and not (isinstance(st.get("cells", 1), int) and 1 <= st.get("cells", 1) <= 41):
            return "cells must be between 1 and 41"
        if do == "fire" and not (isinstance(st.get("count", 1), int) and 1 <= st.get("count", 1) <= 3):
            return "count must be between 1 and 3"
        return None

    def submit(self, side, steps):
        """Queue orders for the next command round. Real-server behaviour: a wrong order rejects only that
        tank's steps, the other tanks are accepted. Returns one result per step."""
        by_tank, results = {}, []
        for st in steps:
            by_tank.setdefault(st.get("tank") if isinstance(st, dict) else None, []).append(st)
        for tid, sts in by_tank.items():
            err = next((e for e in map(self.check_step, sts) if e), None)
            t = self.tank(side, tid) if isinstance(tid, int) and not err else None
            if not err and t is None:
                err = f"tank must be 1-{self.cfg.tanks}"
            elif not err and t.out:
                err = f"tank {tid} has no lives left"
            if not err:
                queued = list(t.plan)
                for st in [p for p in self.pending[side] if p["tank"] == tid] + sts:
                    queued = [] if st["do"] == "stop" else queued + [st]
                if len(queued) > self.cfg.max_queue:
                    err = f"too many steps waiting: max {self.cfg.max_queue} (tank {tid} has {len(t.plan)}); send stop first"
            if err:
                results += [{"tank": tid, "do": st.get("do") if isinstance(st, dict) else None, "ok": False, "error": err} for st in sts]
                continue
            waiting = len(t.plan) + sum(1 for p in self.pending[side] if p["tank"] == tid)
            for st in sts:
                self.pending[side].append(dict(st))
                waiting = 0 if st["do"] == "stop" else waiting + 1
                results.append({"tank": tid, "do": st["do"], "ok": True, "steps_waiting": waiting,
                                "applies_in_round": self.round + 1})
        return results

    def _apply_pending(self):
        for side in ("top", "bottom"):
            for st in self.pending[side]:
                t = self.tank(side, st["tank"])
                if not t or not t.alive:
                    continue
                if st["do"] == "stop":
                    t.plan.clear()
                    t.fired_in_step = 0
                else:
                    t.plan.append(dict(st, _cells_left=st.get("cells", 1)))
            self.pending[side] = []

    # ------------------------------------------------------------ main step
    def start(self):
        self.status = "live"
        self.emit({"type": "match_started", "text": "Trận bắt đầu!"})

    def step(self, dt):
        if self.status != "live":
            return
        self.t += dt
        cfg = self.cfg
        if self.t >= self.next_tick_at:
            self.next_tick_at += cfg.tick_s
            self.round += 1
            self._apply_pending()
        if not self.armor_over and self.armor_left <= 0:
            self.armor_over = True
            for s in ("top", "bottom"):
                if self.shovel_until[s] <= self.t:
                    self._set_ring(s, "#", only_existing=True)
            self.emit({"type": "armor_ended", "text": "Hết giáp: đại bàng hai bên đã có thể bị phá."})
        for s in ("top", "bottom"):  # eagle invulnerability / shovel wear off
            if self.eagle_invuln_until[s] and self.t >= self.eagle_invuln_until[s]:
                self.eagle_invuln_until[s] = 0.0
                if self.armor_over and self.shovel_until[s] <= self.t:
                    self._set_ring(s, "#", only_existing=True)
            if self.shovel_until[s] and self.t >= self.shovel_until[s]:
                self.shovel_until[s] = 0.0
                if self.armor_over and not self.eagle_invuln_until[s]:
                    self._set_ring(s, "#", only_existing=True)
        for t in self.all_tanks():
            self._tank_step(t, dt)
        self._bullets_step(dt)
        self._items_step()
        self._check_end()

    # ------------------------------------------------------------ tanks
    def _occupied_by_tank(self, x, y, me):
        for o in self.all_tanks():
            if o is me or not o.alive:
                continue
            if overlap(x, y, o.x, o.y) or (o.moving_to and overlap(x, y, *o.moving_to)):
                return o
        return None

    def _eagle_at(self, x, y):
        for s, (ex, ey) in EAGLE.items():
            if overlap(x, y, ex, ey):
                return s
        return None

    def _front_cells(self, t, d):
        dx, dy = DIRS[d]
        if dy:
            r = t.y - 1 if dy < 0 else t.y + S
            return [(t.x + i, r) for i in range(S)]
        c = t.x - 1 if dx < 0 else t.x + S
        return [(c, t.y + j) for j in range(S)]

    def _completed(self, t, st, reason):
        self.emit({"type": "order_completed", "tank": t.id, "order": order_str({k: v for k, v in st.items() if k != "_cells_left"}),
                   "reason": reason,
                   "steps_left": len(t.plan)}, to=t.side)

    def _fail(self, t, reason, cell):
        self.emit({"type": "order_failed", "tank": t.id, "side": t.side, "order": order_str(t.plan[0]) if t.plan else None,
                   "reason": reason,
                   "cell": list(cell) if cell else None, "text": f"{self.name(t)}: lệnh thất bại — {reason}"}, to=t.side)
        t.plan.clear()
        t.fired_in_step = 0
        t.doing = "đứng chờ"

    def _tank_step(self, t, dt):
        cfg = self.cfg
        if not t.alive:
            if t.lives > 0 and self.t >= t.dead_until:
                sx, sy = SPAWNS[t.side][t.id - 1]
                if not self._occupied_by_tank(sx, sy, t):
                    t.alive, t.x, t.y = True, sx, sy
                    t.dir = "down" if t.side == "top" else "up"
                    t.shield_until = self.t + cfg.spawn_shield_s
                    self.emit({"type": "tank_respawned", "tank": t.id, "side": t.side, "x": sx, "y": sy,
                               "text": f"{self.name(t)} hồi sinh (mất sao, mất việc)"})
            return
        t.fire_cooldown = max(0.0, t.fire_cooldown - dt)
        if self.in_half_of(OPP[t.side], t.y):
            t.enemy_half_s += dt
        if t.moving_to:  # continue the current one-cell move
            on_ice = self.grid.at(t.x, t.y) == "-"
            t.move_progress += dt * cfg.tank_cells_per_s * (1.3 if on_ice else 1.0)
            if t.move_progress >= 1.0:
                t.x, t.y = t.moving_to
                t.moving_to, t.move_progress = None, 0.0
                if t.plan and t.plan[0]["do"] in ("move", "move_fire") and "_cells_left" in t.plan[0]:
                    t.plan[0]["_cells_left"] -= 1
                    if t.plan[0]["_cells_left"] <= 0:
                        self._completed(t, t.plan.pop(0), "arrived")
                self._pickup(t)
            return
        if not t.plan:
            t.idle_s += dt
            t.doing = "đứng chờ"
            return
        st = t.plan[0]
        do = st["do"]
        if do == "turn":
            t.dir = st["direction"]
            t.doing = "xoay nòng"
            self._completed(t, t.plan.pop(0), f"facing {t.dir}")
            return
        if do == "fire":
            d = st.get("direction") or t.dir
            t.dir = d
            t.doing = "nã đạn"
            limit = 2 if t.stars >= 2 else 1
            if t.fire_cooldown <= 0 and sum(1 for b in self.bullets if b.side == t.side and b.tank_id == t.id) < limit:
                self._shoot(t, d)
                if not t.plan or t.plan[0] is not st:   # point-blank eagle hit sent the tank home (plan cleared)
                    return
                t.fired_in_step += 1
                if t.fired_in_step >= st.get("count", 1):
                    self._completed(t, t.plan.pop(0), "fired")
                    t.fired_in_step = 0
            return
        # move / move_fire
        d = st["direction"]
        t.dir = d
        t.doing = "vừa đi vừa bắn" if do == "move_fire" else "nhích"
        dx, dy = DIRS[d]
        nx, ny = t.x + dx, t.y + dy
        front = self._front_cells(t, d)
        kinds = [self.grid.at(x, y) for x, y in front]
        if "X" in kinds:
            return self._fail(t, "ra ngoài bản đồ", front[0])
        if self._eagle_at(nx, ny):
            return self._fail(t, "bị đại bàng chặn", front[0])
        blocking = [(c, k) for c, k in zip(front, kinds) if k in "#@~"]
        if blocking:
            cell, k = blocking[0]
            if do == "move_fire" and (k == "#" or (k == "@" and t.stars >= 3)):
                if t.fire_cooldown <= 0 and not any(b.side == t.side and b.tank_id == t.id for b in self.bullets):
                    self._shoot(t, d)
                return
            what = {"#": "gạch", "@": "thép", "~": "nước"}[k]
            return self._fail(t, f"bị {what} chặn tại ({cell[0]}, {cell[1]})", cell)
        if self._occupied_by_tank(nx, ny, t):
            t.blocked_for += dt
            if t.blocked_for >= cfg.blocked_wait_s:
                t.blocked_for = 0.0
                self._fail(t, f"bị xe chặn tại ({front[0][0]}, {front[0][1]})", front[0])
            return
        t.blocked_for = 0.0
        t.moving_to, t.move_progress = (nx, ny), 0.0

    def _shoot(self, t, d):
        cfg = self.cfg
        speed = cfg.bullet_fast_cells_per_s if t.stars >= 1 else cfg.bullet_cells_per_s
        dx, dy = DIRS[d]
        # bullet lane origin: the 2-wide strip just in front of the tank
        bx = t.x + (S if dx > 0 else -1 if dx < 0 else 0)
        by = t.y + (S if dy > 0 else -1 if dy < 0 else 0)
        b = Bullet(t.side, t.id, float(bx), float(by), d, speed, t.stars >= 3, t.y, t.x,
                   t.plan[0]["do"] if t.plan else t.doing)
        self.bullets.append(b)
        self._bullet_hit(b)   # the cell right in front of the barrel counts too (point-blank bricks)
        t.fire_cooldown = 0.15

    def _send_home(self, t):
        sx, sy = SPAWNS[t.side][t.id - 1]
        t.x, t.y, t.moving_to, t.move_progress = sx, sy, None, 0.0
        t.plan.clear()
        t.fired_in_step = 0

    def _kill(self, victim, killer_side, killer_id):
        victim.lives -= 1
        victim.deaths += 1
        victim.alive = False
        victim.stars = 0
        victim.plan.clear()
        victim.moving_to, victim.move_progress = None, 0.0
        victim.dead_until = self.t + self.cfg.respawn_s
        if killer_side != victim.side:
            self._add_score(killer_side, 500)
            k = self.tank(killer_side, killer_id)
            if k:
                k.kills += 1
        self.emit({"type": "tank_destroyed", "tank": victim.id, "side": victim.side, "by_tank": killer_id, "by_side": killer_side,
                   "lives_left": victim.lives, "x": victim.x, "y": victim.y,
                   "text": f"Xe {killer_id} {SIDE_VI[killer_side]} hạ Xe {victim.id} {SIDE_VI[victim.side]} (còn {victim.lives} mạng)"})

    def _add_score(self, side, pts):
        self.score[side] += pts
        self.score_reached_at[side] = self.t

    # ------------------------------------------------------------ bullets
    def _lane_cells(self, b):
        dx, dy = DIRS[b.dir]
        x, y = int(b.x // 1), int(b.y // 1)
        return [(x + i, y) for i in range(S)] if dy else [(x, y + j) for j in range(S)]

    def _bullets_step(self, dt):
        for b in self.bullets:
            if b.dead:
                continue
            dist = b.speed * dt
            dx, dy = DIRS[b.dir]
            while dist > 0 and not b.dead:
                stepd = min(0.5, dist)
                dist -= stepd
                b.x += dx * stepd
                b.y += dy * stepd
                self._bullet_hit(b)
        # bullets meeting each other cancel out
        live = [b for b in self.bullets if not b.dead]
        for i, a in enumerate(live):
            for c in live[i + 1:]:
                if not a.dead and not c.dead and set(self._lane_cells(a)) & set(self._lane_cells(c)):
                    a.dead = c.dead = True
        self.bullets = [b for b in self.bullets if not b.dead]

    def _bullet_hit(self, b):
        cells = self._lane_cells(b)
        # tanks
        for o in self.all_tanks():
            if not o.alive:
                continue
            if any(o.x <= cx < o.x + S and o.y <= cy < o.y + S for cx, cy in cells) or \
               (o.moving_to and any(o.moving_to[0] <= cx < o.moving_to[0] + S and o.moving_to[1] <= cy < o.moving_to[1] + S for cx, cy in cells)):
                if o.side == b.side and o.id == b.tank_id:
                    continue
                b.dead = True
                if o.side != b.side and self.t >= o.shield_until:
                    self._kill(o, b.side, b.tank_id)
                return
        # eagles
        for side, (ex, ey) in EAGLE.items():
            if any(ex <= cx < ex + S and ey <= cy < ey + S for cx, cy in cells):
                b.dead = True
                self._eagle_hit(side, b)
                return
        # terrain
        kinds = [self.grid.at(cx, cy) for cx, cy in cells]
        if all(k == "X" for k in kinds):
            b.dead = True
            return
        if "#" in kinds or "@" in kinds:
            for (cx, cy), k in zip(cells, kinds):
                if k == "#" or (k == "@" and b.pierce_steel):
                    self.grid.c[cy][cx] = "."
            b.dead = True

    def _eagle_hit(self, side, b):
        shooter = self.tank(b.side, b.tank_id)
        if b.side == side and shooter:
            shooter.own_eagle_hits += 1
            self.friendly_fire.append({"t": round(self.clock_elapsed, 1), "side": side, "tank": shooter.id,
                                       "from": [b.shooter_x, b.shooter_y], "dir": b.dir, "doing": b.doing})
        if b.side != side and shooter:  # hitting the enemy eagle always sends the shooter home
            shooter.eagle_hits += 1
            self._send_home(shooter)
            self.emit({"type": "sent_home", "tank": shooter.id, "side": shooter.side,
                       "text": f"{self.name(shooter)} trúng đại bàng, bị đưa về nhà (mất việc)"})
        counts = self.t >= self.eagle_invuln_until[side] and self.in_half_of(side, b.shooter_y)
        if not counts:
            why = "đại bàng đang bất tử" if self.t < self.eagle_invuln_until[side] else "bắn từ nửa sân của mình"
            self.emit({"type": "eagle_hit_no_damage", "side": side, "by_tank": b.tank_id, "by_side": b.side, "why": why,
                       "text": f"Trúng đại bàng {SIDE_VI[side]} nhưng không mất máu ({why})"})
            return
        self.base_hp[side] -= 1
        self.eagle_invuln_until[side] = self.t + self.cfg.eagle_invuln_s
        self._set_ring(side, "@", only_existing=True)
        if b.side != side:
            self._add_score(b.side, 1000)
        sx = shooter.x if shooter else None
        self.emit({"type": "eagle_hit", "side": side, "hp_left": self.base_hp[side], "by_tank": b.tank_id, "by_side": b.side,
                   "text": f"Đại bàng {SIDE_VI[side]} trúng đạn! Còn {self.base_hp[side]} máu"})
        self.emit({"type": "base_attacked", "side": side, "by_tank": b.tank_id, "by_side": b.side,
                   "from": [sx, b.shooter_y] if sx is not None else None,
                   "text": f"Căn cứ của bạn bị bắn từ hàng y={b.shooter_y}"}, to=side)

    # ------------------------------------------------------------ items
    def _items_step(self):
        cfg = self.cfg
        self.items = [i for i in self.items if i.until > self.t]
        if self.t >= self.next_item_at:
            self.next_item_at += cfg.item_every_s
            for _ in range(200):
                x, y = self.rng.randrange(0, self.grid.w - S), self.rng.randrange(6, self.grid.h // 2 - S)
                x2, y2 = rotate180(x, y, self.grid.w, self.grid.h)
                ok = all(self.grid.at(x + i, y + j) in ".%-#" and self.grid.at(x2 + i, y2 + j) in ".%-#"
                         for j in range(S) for i in range(S))
                if ok and not any(overlap(x, y, o.x, o.y) or overlap(x2, y2, o.x, o.y) for o in self.all_tanks()):
                    kind = self.rng.choice(ITEM_KINDS)
                    until = self.t + cfg.item_ttl_s
                    for ix, iy in ((x, y), (x2, y2)):
                        self.item_seq += 1
                        self.items.append(Item(kind, ix, iy, until, self.item_seq))
                        self.emit({"type": "powerup_spawned", "id": self.item_seq, "powerup": kind, "x": ix, "y": iy,
                                   "text": f"Vật phẩm {ITEM_ICON[kind]} xuất hiện ở ({ix},{iy})"})
                    break

    def _pickup(self, t):
        for it in list(self.items):
            if overlap(t.x, t.y, it.x, it.y):
                self.items.remove(it)
                t.items += 1
                self._add_score(t.side, 200)
                if it.kind == "star":
                    t.stars = min(3, t.stars + 1)
                elif it.kind == "helmet":
                    t.shield_until = self.t + self.cfg.helmet_s
                elif it.kind == "shovel":
                    self.shovel_until[t.side] = self.t + self.cfg.shovel_s
                    self._set_ring(t.side, "@")
                elif it.kind == "tank":
                    t.lives += 1
                self.emit({"type": "powerup_picked", "id": it.id, "tank": t.id, "side": t.side, "powerup": it.kind,
                           "text": f"{self.name(t)} nhặt {ITEM_ICON[it.kind]}"})

    # ------------------------------------------------------------ end
    def _check_end(self):
        cfg = self.cfg
        for s in ("top", "bottom"):
            if self.base_hp[s] <= 0:
                return self._finish(OPP[s], f"phá căn cứ {SIDE_VI[s]}", "base_destroyed")
        for s in ("top", "bottom"):
            if self.score[s] >= cfg.score_target:
                return self._finish(s, f"đạt {cfg.score_target} điểm", "score_limit")
        for s in ("top", "bottom"):
            if all(t.lives <= 0 and not t.alive for t in self.tanks[s]):
                return self._finish(OPP[s], f"đội {SIDE_VI[s]} hết mạng", "all_tanks_lost")
        if self.time_left <= 0:
            a, b = self.score["top"], self.score["bottom"]
            if a != b:
                return self._finish("top" if a > b else "bottom", "hết giờ, nhiều điểm hơn", "time_up")
            ra, rb = self.score_reached_at["top"], self.score_reached_at["bottom"]
            if ra != rb:
                return self._finish("top" if ra < rb else "bottom", "bằng điểm, đạt điểm đó trước", "time_up_tiebreak")
            for key, why in ((lambda s: self.base_hp[s], "bằng điểm, căn cứ còn nhiều máu hơn"),
                             (lambda s: sum(t.lives for t in self.tanks[s]), "bằng điểm, còn nhiều mạng hơn"),
                             (lambda s: sum(t.enemy_half_s for t in self.tanks[s]), "bằng điểm, ở lâu hơn trong nửa sân đối phương")):
                if key("top") != key("bottom"):
                    return self._finish("top" if key("top") > key("bottom") else "bottom", why, "time_up_tiebreak")
            return self._finish(self.rng.choice(["top", "bottom"]), "bằng điểm, bốc thăm", "time_up_tiebreak")

    def _finish(self, winner, reason, code="finished"):
        self.status = "finished"
        self.winner, self.reason, self.reason_code = winner, reason, code
        self.emit({"type": "match_finished", "winner": winner, "reason": reason, "reason_code": code,
                   "text": f"Hết trận! Đội {SIDE_VI[winner]} thắng ({reason})"})

    # ------------------------------------------------------------ views
    def ascii_map(self, side):
        """Player-relative ASCII: digits = your tanks, O = enemy tanks, H = your eagle, T = enemy eagle, P = items."""
        rows = [list(r) for r in self.grid.rows()]

        def paint(x, y, ch):
            for j in range(S):
                for i in range(S):
                    if 0 <= y + j < self.grid.h and 0 <= x + i < self.grid.w:
                        rows[y + j][x + i] = ch
        for it in self.items:
            paint(it.x, it.y, "P")
        for b in self.bullets:
            bx, by = int(b.x // 1), int(b.y // 1)
            if 0 <= by < self.grid.h and 0 <= bx < self.grid.w:
                rows[by][bx] = "*"
        for s, (ex, ey) in EAGLE.items():
            paint(ex, ey, "x" if self.base_hp[s] <= 0 else "H" if s == side else "T")
        for t in self.all_tanks():
            if t.alive:
                paint(t.x, t.y, str(t.id) if t.side == side else "O")
        return "\n".join("".join(r) for r in rows)

    def tank_json(self, t, mine):
        d = {"id": t.id, "x": t.x, "y": t.y, "dir": t.dir, "lives": t.lives, "stars": t.stars, "alive": t.alive,
             "shield": t.alive and self.t < t.shield_until, "doing": t.doing}
        if not t.alive and t.lives > 0:
            d["respawn_in"] = round(max(0.0, t.dead_until - self.t), 1)
        if mine:
            d["order"] = [{k: v for k, v in s.items() if not k.startswith("_")} | ({"cells_left": s["_cells_left"]} if "_cells_left" in s else {})
                          for s in t.plan]
        return d

    def side_json(self, s, mine):
        ex, ey = EAGLE[s]
        return {"side": s, "score": self.score[s],
                "base": {"x": ex, "y": ey, "hp": self.base_hp[s],
                         "invulnerable": self.t < self.eagle_invuln_until[s] or not self.armor_over or self.t < self.shovel_until[s]},
                "tanks": [self.tank_json(t, mine) for t in self.tanks[s]]}

    def state_for(self, side, since_seq=0, with_map=True):
        """GET /state in the real server's format (sample from tank.recoff.cloud, Nov 2026)."""
        opp = OPP[side]

        def ev_out(e):
            e = {k: v for k, v in e.items() if k not in ("text", "t", "side") or (k == "side" and e.get("type") == "base_attacked")}
            if e.get("type") == "match_finished":
                e = {"type": "match_finished", "outcome": "you_won" if e.get("winner") == side else "you_lost",
                     "reason": e.get("reason_code") or e.get("reason"), "your_score": self.score[side], "opponent_score": self.score[opp]}
            return e

        evs = []
        for seq, t, to, e in self.events:
            if seq > since_seq and (to is None or to == side):
                evs.append({"seq": seq, **ev_out(e), "time_left_s": round(max(0.0, self.cfg.game_seconds - e.get("t", 0)))})

        def tank(t, mine):
            px = t.x + (t.moving_to[0] - t.x) * t.move_progress if t.moving_to else t.x
            py = t.y + (t.moving_to[1] - t.y) * t.move_progress if t.moving_to else t.y
            d = {"tank": t.id, "alive": t.alive, "lives": t.lives}
            if not t.alive:  # real server: a dead tank only has respawns_in_s
                if t.lives > 0:
                    d["respawns_in_s"] = round(max(0.0, t.dead_until - self.t) / self.cfg.time_scale, 1)
                return d
            if mine:
                d["order"] = order_obj(t.plan[0]) if t.plan else None
                d["next"] = [order_str(p) for p in t.plan[1:]]
            d.update({"x": round(float(px), 2), "y": round(float(py), 2), "facing": t.dir, "stars": t.stars,
                      "shielded": t.alive and self.t < t.shield_until})
            return d

        def base(s):
            ex, ey = EAGLE[s]
            steel = 0.0
            if not self.armor_over:
                steel = self.armor_left
            steel = max(steel, (self.eagle_invuln_until[s] - self.t) * self.cfg.clock_rate, (self.shovel_until[s] - self.t) * self.cfg.clock_rate)
            return {"x": ex, "y": ey, "alive": self.base_hp[s] > 0, "hp": self.base_hp[s],
                    "invulnerable": self.t < self.eagle_invuln_until[s], "steel_walls_s": round(max(0.0, steel))}

        st = {
            "status": self.status,
            "time_left_s": round(self.time_left),
            "bases_open_in_s": round(self.armor_left),
            "score_limit": self.cfg.score_target,
            "round": self.round,
            "next_round_in_s": round(max(0.0, self.next_tick_at - self.t) / self.cfg.time_scale, 2),
            "map_size": {"width": self.grid.w, "height": self.grid.h},
            "you": {"side": side, "score": self.score[side], "tanks": [tank(t, True) for t in self.tanks[side]]},
            "opponent": {"side": opp, "score": self.score[opp], "tanks": [tank(t, False) for t in self.tanks[opp]]},
            "bases": {"yours": base(side), "opponent": base(opp)},
            "npcs": [],
            "bullets": [{"x": round(b.x, 2), "y": round(b.y, 2), "facing": b.dir, "owner": "you" if b.side == side else "opponent"}
                        for b in self.bullets],
            "powerups": [{"id": i.id, "type": i.kind, "x": i.x, "y": i.y, "expires_in_s": round(i.until - self.t, 1)} for i in self.items],
        }
        if self.status == "finished":
            st["result"] = {"outcome": "you_won" if self.winner == side else "you_lost", "reason": self.reason_code or self.reason,
                            "your_score": self.score[side], "opponent_score": self.score[opp]}
        if with_map:
            st["map"] = self.ascii_map(side).split("\n")
            st["map_legend"] = MAP_LEGEND
        st["events"] = evs
        return st, self.seq

    def view(self):
        """Full snapshot for the web viewer (both sides)."""
        return {
            "status": self.status, "t": round(self.t, 2), "map_name": self.map_name,
            "time_left": round(self.time_left, 1), "armor_left": round(self.armor_left, 1),
            "grid": self.grid.rows(), "score": self.score, "base_hp": self.base_hp,
            "eagles": {s: {"x": EAGLE[s][0], "y": EAGLE[s][1], "invuln": self.t < self.eagle_invuln_until[s]} for s in EAGLE},
            "tanks": [{"side": t.side, "id": t.id, "x": t.x, "y": t.y, "dir": t.dir, "alive": t.alive, "lives": t.lives,
                       "stars": t.stars, "shield": t.alive and self.t < t.shield_until, "doing": t.doing,
                       "to": list(t.moving_to) if t.moving_to else None, "p": round(t.move_progress, 2),
                       "kills": t.kills, "deaths": t.deaths, "items": t.items, "eagle_hits": t.eagle_hits, "own_eagle_hits": t.own_eagle_hits,
                       "idle_s": round(t.idle_s, 1), "plan": len(t.plan)} for t in self.all_tanks()],
            "bullets": [{"x": round(b.x, 2), "y": round(b.y, 2), "dir": b.dir, "side": b.side} for b in self.bullets],
            "items": [{"kind": i.kind, "x": i.x, "y": i.y} for i in self.items],
            "events": [e for _, _, to, e in self.events[-40:] if to is None],
            "winner": self.winner, "reason": self.reason, "friendly_fire": self.friendly_fire[-20:],
        }


# ----------------------------------------------------------------------------- bots
class Bot:
    """Bot luyện tập. Dễ: cả đội giữ nhà (nghĩ 4s) · Vừa: nửa công nửa thủ (2s) · Khó: như Vừa + xe công gặp
    địch là đánh, xe thủ chặn mọi xe vào nửa sân nhà, nhặt đồ gần đường, nhà còn 2 máu thì rút hết về thủ (0,1s).
    Ra lệnh bằng đúng move / move_fire / turn / fire / stop qua Game.submit, như agent."""

    THINK = {"easy": 4.0, "medium": 2.0, "hard": 0.1}

    def __init__(self, game: Game, side: str, level: str = "medium"):
        self.g, self.side, self.level = game, side, level
        self.next_think = 0.0
        self.goal = {}

    def tick(self):
        g = self.g
        if g.status != "live" or g.t < self.next_think:
            return
        self.next_think = g.t + self.THINK.get(self.level, 2.0)
        steps = []
        for t in g.tanks[self.side]:
            if t.alive:
                steps += self._decide(t)
        if steps:
            g.submit(self.side, steps)

    # --- helpers
    def _enemy(self):
        return [t for t in self.g.tanks[OPP[self.side]] if t.alive]

    def _lane_to(self, t, tx, ty, size=S, max_bricks=0):
        """Direction to shoot from t at a 2x2/size target if aligned with a clear lane, else None."""
        g = self.g
        if abs(t.x - tx) < S and tx + size > t.x - (S - 1):
            d = "up" if ty < t.y else "down"
        elif abs(t.y - ty) < S:
            d = "left" if tx < t.x else "right"
        else:
            return None
        dx, dy = DIRS[d]
        x, y, bricks = t.x, t.y, 0
        for _ in range(42):
            x, y = x + dx, y + dy
            if overlap(x, y, tx, ty, S, size):
                return d
            for o in g.all_tanks():
                if o.alive and o is not t and overlap(x, y, o.x, o.y) and not overlap(o.x, o.y, tx, ty, S, size):
                    return None
            cells = g._front_cells(type("P", (), {"x": x - dx, "y": y - dy})(), d)
            kinds = [g.grid.at(cx, cy) for cx, cy in cells]
            if "X" in kinds or "@" in kinds and t.stars < 3:
                return None
            if "#" in kinds:
                bricks += 1
                if bricks > max_bricks:
                    return None
        return None

    def _touches_own_wall(self, x, y):
        """Anchor overlapping a still-standing wall cell around our own eagle (don't shoot our own wall)."""
        g = self.g
        return any(overlap(x, y, cx, cy, S, 1) and g.grid.at(cx, cy) in "#@" for cx, cy in g.ring[self.side])

    def _hits_own_eagle(self, t, d):
        """Would a shot from t toward d reach our own eagle if it misses? (bullets hit anything)"""
        g, (hx, hy) = self.g, EAGLE[self.side]
        dx, dy = DIRS[d]
        x, y = t.x, t.y
        for _ in range(42):
            cells = g._front_cells(type("P", (), {"x": x, "y": y})(), d)
            if any(hx <= cx < hx + S and hy <= cy < hy + S for cx, cy in cells):
                return True
            kinds = [g.grid.at(cx, cy) for cx, cy in cells]
            if "X" in kinds or ("@" in kinds and t.stars < 3):
                return False
            x, y = x + dx, y + dy
        return False

    def _path_steps(self, t, goal_fn, limit=1500):
        g = self.g
        start = (t.x, t.y)
        prev, q, found = {start: None}, deque([start]), None
        while q and len(prev) < limit:
            cur = q.popleft()
            if goal_fn(*cur):
                found = cur
                break
            for d, (dx, dy) in DIRS.items():
                n = (cur[0] + dx, cur[1] + dy)
                if n in prev or not g.grid.fits(*n, allow_brick=True) or g._eagle_at(*n) or self._touches_own_wall(*n):
                    continue
                prev[n] = (cur, d)
                q.append(n)
        if not found or found == start:
            return None
        path = []
        while prev[found]:
            (p, d) = prev[found]
            path.append((d, found))
            found = p
        path.reverse()
        steps = []
        for d, (x, y) in path:
            brick = not g.grid.fits(x, y)
            do = "move_fire" if brick else "move"
            if steps and steps[-1]["direction"] == d and steps[-1]["do"] == do:
                steps[-1]["cells"] += 1
            else:
                if len(steps) >= 3:
                    break
                steps.append({"tank": t.id, "do": do, "direction": d, "cells": 1})
        return steps

    def _go(self, t, key, goal_fn):
        if self.goal.get(t.id) == key and t.plan:
            return []
        steps = self._path_steps(t, goal_fn)
        if not steps:
            return []
        self.goal[t.id] = key
        return [{"tank": t.id, "do": "stop"}] + steps

    def _shoot(self, t, d, n=2):
        self.goal[t.id] = None
        return [{"tank": t.id, "do": "stop"}, {"tank": t.id, "do": "fire", "direction": d, "count": n}]

    def _decide(self, t):
        g, lvl = self.g, self.level
        home, enemy_side = self.side, OPP[self.side]
        ex, ey = EAGLE[enemy_side]
        hx, hy = EAGLE[home]
        # 1) enemy tank in a clear lane → shoot
        for e in self._enemy():
            d = self._lane_to(t, e.x, e.y)
            if d and abs(e.x - t.x) + abs(e.y - t.y) <= (12 if lvl == "hard" else 8) and not self._hits_own_eagle(t, d):
                return self._shoot(t, d)
        attackers = {"easy": set(), "medium": {1, 2}, "hard": {1, 2}}.get(lvl, {1, 2})
        if lvl == "hard" and g.base_hp[home] <= 2:
            attackers = set()
        # 2) hard: detour for a close item
        if lvl == "hard":
            for it in g.items:
                if abs(it.x - t.x) + abs(it.y - t.y) <= 8:
                    return self._go(t, ("item", it.x, it.y), lambda x, y, it=it: overlap(x, y, it.x, it.y))
        if t.id in attackers:
            # hard attackers fight enemies they meet
            if lvl == "hard":
                near = [e for e in self._enemy() if abs(e.x - t.x) + abs(e.y - t.y) <= 10]
                if near:
                    e = near[0]
                    return self._go(t, ("hunt", e.id, e.x // 4, e.y // 4),
                                    lambda x, y, e=e: (abs(x - e.x) < S or abs(y - e.y) < S) and abs(x - e.x) + abs(y - e.y) >= 3)
            # attack the eagle from the enemy half
            fake = type("P", (), {})
            if g.in_half_of(enemy_side, t.y):
                probe = fake()
                probe.x, probe.y, probe.stars = t.x, t.y, t.stars
                d = self._lane_to(probe, ex, ey, max_bricks=2)
                if d and g.t >= g.eagle_invuln_until[enemy_side] and g.armor_over:
                    return self._shoot(t, d, 3)
                if d:
                    if t.dir != d:
                        self.goal[t.id] = None
                        return [{"tank": t.id, "do": "stop"}, {"tank": t.id, "do": "turn", "direction": d}]
                    return []

            def spot(x, y):
                if not g.in_half_of(enemy_side, y) or not (abs(x - ex) < S or abs(y - ey) < S):
                    return False
                probe = fake()
                probe.x, probe.y, probe.stars = x, y, 3
                return self._lane_to(probe, ex, ey, max_bricks=2) is not None
            return self._go(t, ("eagle",), spot)
        # defenders
        radius = 30 if lvl == "hard" else 14
        invaders = [e for e in self._enemy() if g.in_half_of(home, e.y) and abs(e.x - hx) + abs(e.y - hy) <= radius]
        if invaders:
            e = min(invaders, key=lambda e: abs(e.x - hx) + abs(e.y - hy))
            return self._go(t, ("intercept", e.id, e.x // 4, e.y // 4),
                            lambda x, y, e=e: (abs(x - e.x) < S or abs(y - e.y) < S) and abs(x - e.x) + abs(y - e.y) >= 3)
        posts = [(hx - 4, hy), (hx + 4, hy), (hx - 2, hy - 4 if home == "bottom" else hy + 4), (hx + 2, hy - 4 if home == "bottom" else hy + 4)]
        px, py = posts[(t.id - 1) % len(posts)]
        if (t.x, t.y) == (px, py) or abs(t.x - px) + abs(t.y - py) <= 1:
            face = "up" if home == "bottom" else "down"
            if t.dir != face and not t.plan:
                return [{"tank": t.id, "do": "turn", "direction": face}]
            return []
        return self._go(t, ("post", px, py), lambda x, y: abs(x - px) + abs(y - py) <= 1)


def now():
    return time.monotonic()


MAP_LEGEND = (". empty   # brick (destructible)   @ steel (only a 3-star tank breaks it)   ~ water (tanks cannot cross, bullets can)"
              "   % bush (hides tanks)   - ice (slippery)   1 2 your tanks (by number)   O opponent tank   N NPC tank   H your base"
              "   T opponent base   x destroyed base   P power-up   * bullet")


def order_obj(st):
    """Current order as the real server shows it: {"type", "direction", "cells", "status": "active"}."""
    d = {"type": st.get("do")}
    if st.get("direction"):
        d["direction"] = st["direction"]
    if st.get("do") in ("move", "move_fire"):
        d["cells"] = st.get("_cells_left", st.get("cells", 1))
    if st.get("do") == "fire":
        d["count"] = st.get("count", 1)
    d["status"] = "active"
    return d


def order_str(st):
    """Order as the real server prints it: move(up, 3) · move_fire(left, 2) · turn(down) · fire(3, down) · stop."""
    do = st.get("do")
    if do in ("move", "move_fire"):
        return f"{do}({st.get('direction')}, {st.get('_cells_left', st.get('cells', 1))})"
    if do == "turn":
        return f"turn({st.get('direction')})"
    if do == "fire":
        return f"fire({st.get('count', 1)}, {st.get('direction') or 'facing'})"
    return str(do)
