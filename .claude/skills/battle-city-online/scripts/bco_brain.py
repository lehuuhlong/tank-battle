"""Battle City Online — bộ não: khối chiến thuật (tactics), các chiến thuật (strategies), runner gửi lệnh."""
import json
import os
import sys
import time
from dataclasses import dataclass, field

from bco_core import (DELTA, DIRS, OVERLAY_CHARS, ApiError, build_path, dijkstra, event_tank, event_type,
                      find_rejections, overlaps, path_to_steps, trace)

STRATEGIES = {}


def register(cls):
    STRATEGIES[cls.name] = cls
    return cls


def get_strategy(name, **params):
    if name not in STRATEGIES:
        raise SystemExit(f"Không có chiến thuật '{name}'. Có: {', '.join(sorted(STRATEGIES))}")
    return STRATEGIES[name](**params)

# ======================================================================
# TACTICS
# ======================================================================
ITEM_PRIORITY = {"star": 5, "⭐": 5, "helmet": 3, "⛑️": 3, "shield": 3, "shovel": 4, "⛏️": 4, "spade": 4,
                 "life": 4, "tank": 4, "🛡️": 4, "extra_life": 4}


@dataclass
class Decision:
    job: str
    steps: list = field(default_factory=list)
    urgent: bool = False
    note: str = ""


def hold(job="hold", note=""):
    return Decision(job=job, steps=[], note=note)


# ---------------------------------------------------------------- world helpers
def entities_for(game, shooter):
    ents = [("my_base", game.my_base, None), ("enemy_base", game.enemy_base, None)]
    ents += [("ally", t.pos, t) for t in game.alive_tanks(True) if t.id != shooter.id]
    ents += [("enemy", t.pos, t) for t in game.alive_tanks(False)]
    return ents


def star3(t):
    return (t.stars or 0) >= 3


def danger_lanes(game, rng=22):
    """Anchors an enemy tank could shoot right now (straight lane, no steel/brick in between)."""
    s, out = game.tank_size, {}
    for e in game.alive_tanks(False):
        for d in DIRS:
            dx, dy = DELTA[d]
            for k in range(1, rng + 1):
                x, y = e.x + dx * k, e.y + dy * k
                cells = game.grid.footprint(x, y, s)
                if any(c in ("steel", "brick", "wall") for c in cells):
                    break
                for off in range(-s + 1, s):  # anchors whose footprint overlaps the lane
                    a = (x + off, y) if dy else (x, y + off)
                    out[a] = out.get(a, 0) + 1
    return out


def penalties_for(game, tank):
    """Avoid anchors overlapping other tanks (allies move: soft cost; enemies bigger) and enemy firing lanes."""
    s, pen = game.tank_size, {}
    danger_w = game.schema.get("danger_penalty", 3)
    if danger_w:
        for a, n in danger_lanes(game).items():
            pen[a] = danger_w * n
    for other, cost in [(t, 6) for t in game.alive_tanks(True) if t.id != tank.id] + [(t, 15) for t in game.alive_tanks(False)]:
        for dx in range(-s + 1, s):
            for dy in range(-s + 1, s):
                a = (other.x + dx, other.y + dy)
                pen[a] = max(pen.get(a, 0), cost)
    return pen


def paths_from(game, tank, memory=None, max_cost=None):
    key = ("paths", tank.id, tank.pos, tank.stars, id(game))
    if memory is not None and memory.get("_path_cache_key") == key:
        return memory["_path_cache"]
    res = dijkstra(game.grid, tank.pos, game.tank_size, star3(tank), penalties_for(game, tank), max_cost)
    if memory is not None:
        memory["_path_cache_key"], memory["_path_cache"] = key, res
    return res


def eagle_vulnerable(game, memory):
    """Time-based check only; armor/post-hit steel walls are in the map and stop bullets in trace()."""
    inv = game.schema.get("eagle_invulnerable_s", 5.0)
    return game.now - memory.get("last_eagle_hit", -1e9) > inv + 0.4


# ---------------------------------------------------------------- shooting
def fire_step(d, count):
    return {"do": "fire", "direction": d, "count": max(1, min(3, count))}


def shot_at(game, tank, d, max_range=60):
    return trace(game.grid, tank.pos, d, game.tank_size, entities_for(game, tank), star3(tank), max_range)


def opportunistic_fire(game, tank, memory, max_bricks=0, max_range=18, allow_base=True):
    """Shoot an enemy tank (or the enemy eagle when it counts) already in a straight lane."""
    best = None
    for d in DIRS:
        hit = shot_at(game, tank, d, max_range)
        if hit["kind"] == "enemy" and hit["bricks"] <= max_bricks and not hit["obj"].shield:
            score = 100 - hit["dist"]
            if best is None or score > best[0]:
                best = (score, d, hit["bricks"] + 1 + (1 if hit["dist"] > 10 else 0), f"bắn xe địch {hit['obj'].id}")
        elif (allow_base and hit["kind"] == "enemy_base" and hit["bricks"] <= 2
              and game.in_enemy_half(tank.y) and eagle_vulnerable(game, memory)):
            best = (1000, d, 3, "bắn đại bàng")
    if best:
        _, d, count, note = best
        return Decision(job=f"shoot:{d}", steps=[fire_step(d, count)], urgent=True, note=note)
    return None


# ---------------------------------------------------------------- reflexes (fast, every cycle)
def can_fire(game, tank, memory):
    return game.now - memory.get("fire_at", {}).get(tank.id, -1e9) >= memory.get("fire_gap", 0.6)


def lead_fire(game, tank, memory, max_range=14):
    """Bắn đón: an enemy moving across one of our lanes will be in it when the bullet arrives.

    Uses enemy velocity estimated by the runner (memory["enemy_vel"], cells/s). Bullet speed and
    command latency come from state-schema.json (bullet_cells_per_s, reaction_latency_s)."""
    vel = memory.get("enemy_vel") or {}
    bs = float(game.schema.get("bullet_cells_per_s", 8.0))
    lat = float(game.schema.get("reaction_latency_s", 0.6))
    s = game.tank_size
    for e in game.alive_tanks(False):
        v = vel.get(e.id)
        if e.shield or not v or abs(v[0]) + abs(v[1]) < 0.8:
            continue
        for d in DIRS:
            dx, dy = DELTA[d]
            if (dx and abs(v[0]) >= abs(v[1])) or (dy and abs(v[1]) >= abs(v[0])):
                continue  # only lanes the enemy crosses (perpendicular to its motion)
            if (dy and abs(e.x - tank.x) < s) or (dx and abs(e.y - tank.y) < s):
                continue  # already in the lane: opportunistic_fire handles it
            along = (e.x - tank.x) * dx + (e.y - tank.y) * dy
            if along < s or along > max_range:
                continue
            t_hit = lat + (along - s + 1) / bs
            px, py = round(e.x + v[0] * t_hit), round(e.y + v[1] * t_hit)
            if (dy and abs(px - tank.x) >= s) or (dx and abs(py - tank.y) >= s):
                continue
            if game.grid.enter_cost(px, py, s) is None:
                continue  # it cannot get there (wall/water)
            ents = [(k, (px, py) if o is e else a, o) for k, a, o in entities_for(game, tank)]
            hit = trace(game.grid, tank.pos, d, s, ents, star3(tank), max_range + 2)
            if hit["kind"] == "enemy" and hit["obj"] is e and hit["bricks"] == 0:
                return Decision(job=f"lead:{d}", steps=[fire_step(d, 2)], urgent=True, note=f"bắn đón xe địch {e.id}")
    return None


def threats_to(game, tank, rng=16):
    """Enemies that can hit `tank` right now: straight lane, nothing but air between, facing it
    (or close enough to turn and shoot within a tick). Returns [(dist, enemy, dir_from_enemy)]."""
    s, out = game.tank_size, []
    for e in game.alive_tanks(False):
        if abs(e.x - tank.x) < s:
            d = "down" if tank.y > e.y else "up"
            dist = abs(tank.y - e.y)
        elif abs(e.y - tank.y) < s:
            d = "right" if tank.x > e.x else "left"
            dist = abs(tank.x - e.x)
        else:
            continue
        if dist > rng or (e.dir and e.dir != d and dist > 6):
            continue
        ents = [("mine", t.pos, t) for t in game.alive_tanks(True)]
        ents += [("other", o.pos, o) for o in game.alive_tanks(False) if o is not e]
        ents += [("base", game.my_base, None), ("base", game.enemy_base, None)]
        hit = trace(game.grid, e.pos, d, s, ents, star3(e), rng + 2)
        if hit["kind"] == "mine" and hit["obj"] is tank and hit["bricks"] == 0:
            out.append((dist, e, d))
    return sorted(out, key=lambda t: t[0])


def dodge(game, tank, memory):
    """Né: step sideways out of every threatening lane, to a free cell not in another enemy lane."""
    if tank.shield:
        return None
    threats = threats_to(game, tank)
    if not threats:
        return None
    s, danger = game.tank_size, danger_lanes(game)
    others = [t for t in game.alive_tanks(True) if t.id != tank.id] + game.alive_tanks(False)
    best = None
    for dd in DIRS:
        dx, dy = DELTA[dd]
        for n in range(1, 5):
            x, y = tank.x + dx * n, tank.y + dy * n
            ec = game.grid.enter_cost(x, y, s)
            if ec is None or ec[1] or any(abs(o.x - x) < s and abs(o.y - y) < s for o in others):
                break  # blocked (brick needs shooting: too slow for a dodge)
            if any((abs(e.x - x) < s) if d in ("up", "down") else (abs(e.y - y) < s) for _, e, d in threats):
                continue  # still in a threatening lane: keep sliding
            score = n + 3 * danger.get((x, y), 0)
            if best is None or score < best[0]:
                best = (score, dd, n)
            break
    if not best:
        return None
    _, dd, n = best
    return Decision(job=f"dodge:{dd}{n}@{tank.x},{tank.y}", steps=[{"do": "move", "direction": dd, "cells": n}],
                    note=f"né xe địch {threats[0][1].id}")


def reflex(game, tank, memory, max_bricks=0, max_range=18, allow_base=False, dodge_ok=True):
    """Phản xạ mỗi nhịp, theo thứ tự: bắn ngay (địch đã thẳng hàng) → bắn đón (địch sắp cắt ngang làn)
    → né (bị nhắm mà chưa bắn trả được). None = không có gì gấp, làm việc của vai."""
    if can_fire(game, tank, memory):
        dec = opportunistic_fire(game, tank, memory, max_bricks, max_range, allow_base) or lead_fire(game, tank, memory)
        if dec:
            return dec
    if not dodge_ok:
        return None
    dec = dodge(game, tank, memory)
    if dec is None and threats_to(game, tank):
        # boxed in (no free side cell) while targeted: shoot back now rather than wait for the cooldown
        dec = opportunistic_fire(game, tank, memory, max_bricks, max_range, allow_base)
        if dec:
            dec.job, dec.note = dec.job + ":boxed", "bị kẹp, bắn trả"
    return dec


def spot_side(spot, d, eagle):
    if d in ("left", "right"):
        return "left" if spot[0] < eagle[0] else "right"
    return "front"


def firing_spots(game, tank, target_anchor, target_kind, memory, max_bricks=1, need_enemy_half=False,
                 min_dist=1, exclude=(), max_candidates=400, prefer=None, assume_star3=False):
    """Anchors from which `tank` could hit `target_kind` at `target_anchor`, sorted by travel cost."""
    s = game.tank_size
    dist, prev = paths_from(game, tank, memory)
    tx, ty = target_anchor
    out = []
    for (x, y), cost in dist.items():
        if not (abs(x - tx) < s or abs(y - ty) < s):
            continue
        if need_enemy_half and not game.in_enemy_half(y):
            continue
        if any(overlaps(x, y, ex, ey, s + 2) for ex, ey in exclude):
            continue
        if abs(x - tx) < s:
            d = "up" if ty < y else "down"
        else:
            d = "left" if tx < x else "right"
        # trace from the candidate, pretending the shooter stands there
        if prefer and prefer != spot_side((x, y), d, (tx, ty)) and not (prefer == "side" and d in ("left", "right")):
            continue
        ghost = type(tank)(id=tank.id, x=x, y=y, stars=tank.stars)
        hit = trace(game.grid, (x, y), d, s, entities_for(game, ghost), star3(tank) or assume_star3)
        if hit["kind"] != target_kind or hit["anchor"] != tuple(target_anchor):
            continue
        if hit["bricks"] > max_bricks or hit["dist"] < min_dist:
            continue
        out.append((cost + 2 * hit["bricks"], (x, y), d, hit["bricks"]))
        if len(out) >= max_candidates:
            break
    out.sort()
    return out, prev


def sticky(memory, key, options, margin=8):
    """Keep last cycle's choice unless a new option is better by > margin (stops flip-flopping)."""
    best = options[0]
    prev = memory.get(key)
    for o in options:
        if o[1] == prev and o[0] <= best[0] + margin:
            best = o
            break
    memory[key] = best[1]
    return best


def go_to(game, tank, goal, prev, job, max_steps=5, then=None):
    path = build_path(prev, tank.pos, goal)
    if path is None:
        return None
    steps = path_to_steps(game.grid, path, game.tank_size, star3(tank), max_steps)
    if then and len(steps) < max_steps and len(path) - 1 == sum(st["cells"] for st in steps):
        steps.append(then)
    return Decision(job=job, steps=steps)


# ---------------------------------------------------------------- roles
def attack_base(game, tank, memory, coordinate=False, taken=None, prefer=None):
    """Move to a firing spot in the enemy half and shoot the eagle when it counts.

    coordinate=True: team-wide 5s rotation (siege) — only fire if no teammate fired recently.
    """
    taken = taken if taken is not None else []
    spots, prev = firing_spots(game, tank, game.enemy_base, "enemy_base", memory,
                               max_bricks=2, need_enemy_half=True, exclude=taken, prefer=prefer)
    if not spots and prefer:
        spots, prev = firing_spots(game, tank, game.enemy_base, "enemy_base", memory,
                                   max_bricks=2, need_enemy_half=True, exclude=taken)
    if not spots:
        return None
    cost, spot, d, bricks = sticky(memory, ("eagle_spot", tank.id), spots)
    taken.append(spot)
    if tank.pos == spot:
        ready = eagle_vulnerable(game, memory)
        if coordinate:
            gap = game.schema.get("eagle_invulnerable_s", 5.0) + 0.6
            ready = ready and game.now - memory.get("team_eagle_shot", -1e9) > gap
        if ready:
            memory["team_eagle_shot"] = game.now
            return Decision(job=f"eagle_fire:{spot}", steps=[fire_step(d, bricks + 1)], urgent=True, note="bắn đại bàng")
        if tank.dir != d:
            return Decision(job=f"eagle_aim:{spot}", steps=[{"do": "turn", "direction": d}], note="ngắm đại bàng, chờ lượt")
        return hold(f"eagle_wait:{spot}", "chờ đại bàng hết bất tử / chờ lượt")
    return go_to(game, tank, spot, prev, f"eagle_go:{spot}", then={"do": "turn", "direction": d})


def stage(game, tank, memory, taken=None, prefer=None):
    """During armor: take a firing spot at the enemy eagle but do not shoot it.
    Spots are computed as if the armor steel were brick (it turns back to brick when armor ends)."""
    taken = taken if taken is not None else []
    spots, prev = firing_spots(game, tank, game.enemy_base, "enemy_base", memory, max_bricks=3,
                               need_enemy_half=True, exclude=taken, prefer=prefer, assume_star3=True)
    if not spots and prefer:
        spots, prev = firing_spots(game, tank, game.enemy_base, "enemy_base", memory, max_bricks=3,
                                   need_enemy_half=True, exclude=taken, assume_star3=True)
    if not spots:
        return None
    _, spot, d, _ = sticky(memory, ("eagle_spot", tank.id), spots)
    taken.append(spot)
    if tank.pos == spot:
        return Decision(job=f"stage_aim:{spot}", steps=[] if tank.dir == d else [{"do": "turn", "direction": d}])
    return go_to(game, tank, spot, prev, f"stage_go:{spot}", then={"do": "turn", "direction": d})


def hunt(game, tank, memory, target=None):
    enemies = [e for e in game.alive_tanks(False) if not e.shield] or game.alive_tanks(False)
    if not enemies:
        return None
    target = target or min(enemies, key=lambda e: abs(e.x - tank.x) + abs(e.y - tank.y))
    spots, prev = firing_spots(game, tank, target.pos, "enemy", memory, max_bricks=1, min_dist=2)
    if not spots:
        return None
    _, spot, d, bricks = spots[0]
    if tank.pos == spot:
        return Decision(job=f"hunt_fire:{target.id}", steps=[fire_step(d, bricks + 1)], urgent=True)
    # target moves: label by target + coarse area so we re-plan when it relocates
    return go_to(game, tank, spot, prev, f"hunt:{target.id}@{target.x // 6},{target.y // 6}",
                 max_steps=3, then=fire_step(d, bricks + 1))


def defense_posts(game, memory, n=4):
    """Pick n anchors near our eagle with long open firing lanes into our half (cached per game)."""
    key = ("posts", game.my_side, game.grid.W, game.grid.H)
    if memory.get("_posts_key") == key:
        return memory["_posts"]
    s, bx, by = game.tank_size, *game.my_base
    cands = []
    for y in range(game.grid.H - s + 1):
        for x in range(game.grid.W - s + 1):
            md = abs(x - bx) + abs(y - by)
            if not (3 <= md <= 12) or not game.in_my_half(y) or game.grid.enter_cost(x, y, s) is None:
                continue
            if game.grid.enter_cost(x, y, s)[1]:
                continue
            ghost_ents = [("my_base", game.my_base, None)]
            cover = 0
            for d in DIRS:
                hit = trace(game.grid, (x, y), d, s, ghost_ents)
                if hit["kind"] == "my_base":
                    cover = -1
                    break
                cover += hit["dist"] - 3 * hit["bricks"]
            if cover > 0:
                cands.append((cover - 2 * md, (x, y)))
    cands.sort(reverse=True)
    posts = []
    for _, p in cands:
        if all(abs(p[0] - q[0]) + abs(p[1] - q[1]) >= 6 for q in posts):
            posts.append(p)
        if len(posts) >= n:
            break
    memory["_posts_key"], memory["_posts"] = key, posts
    return posts


def defend(game, tank, memory, post_index=0, intercept_radius=14, post=None):
    # our eagle was just hit (server also reports the shooter): widen the intercept net for 8s
    if game.now - memory.get("my_base_hit", -1e9) < 8:
        intercept_radius = 24
    # intercept enemies close to our eagle first
    threats = [e for e in game.alive_tanks(False)
               if game.in_my_half(e.y) and abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]) <= intercept_radius]
    if threats:
        target = min(threats, key=lambda e: abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]))
        dec = hunt(game, tank, memory, target)
        if dec:
            dec.job = "intercept:" + dec.job
            return dec
    if post is None:
        posts = defense_posts(game, memory)
        if not posts:
            return hold("defend:no_post")
        post = posts[post_index % len(posts)]
    if tank.pos == post:
        enemies = game.alive_tanks(False)
        if enemies:
            e = min(enemies, key=lambda e: abs(e.x - post[0]) + abs(e.y - post[1]))
            d = ("up" if e.y < post[1] else "down") if abs(e.y - post[1]) >= abs(e.x - post[0]) else ("left" if e.x < post[0] else "right")
            if shot_at(game, tank, d)["kind"] != "my_base" and tank.dir != d:
                return Decision(job=f"guard_aim:{post}:{d}", steps=[{"do": "turn", "direction": d}])
        return hold(f"guard:{post}")
    _, prev = paths_from(game, tank, memory)
    return go_to(game, tank, post, prev, f"guard_go:{post}")


def front_post(game, n=3):
    """Anchor n cells in front of our eagle (towards the enemy), or the nearest free anchor to it."""
    s, (bx, by) = game.tank_size, game.my_base
    ty = by - s - n if game.my_side == "bottom" else by + s + n
    best = None
    for y in range(max(0, ty - 4), min(game.grid.H - s, ty + 4) + 1):
        for x in range(max(0, bx - 6), min(game.grid.W - s, bx + 6) + 1):
            ec = game.grid.enter_cost(x, y, s)
            if ec and not ec[1]:
                dd = abs(x - bx) + abs(y - ty)
                if best is None or dd < best[0]:
                    best = (dd, (x, y))
    return best[1] if best else None


def guard_front(game, tank, memory, n=3):
    post = front_post(game, n)
    dec = defend(game, tank, memory, post=post) if post else None
    if dec and dec.job.startswith("guard:") and tank.pos == post:
        face = "up" if game.my_side == "bottom" else "down"
        s = game.tank_size
        if tank.dir != face and not any(abs(e.x - post[0]) < s or abs(e.y - post[1]) < s for e in game.alive_tanks(False)):
            return Decision(job=f"guard_face:{post}", steps=[{"do": "turn", "direction": face}])
    return dec


def collect(game, tank, memory, max_cost=30, kinds=None, claimed=None):
    claimed = claimed if claimed is not None else set()
    if not game.items:
        return None
    s = game.tank_size
    dist, prev = paths_from(game, tank, memory)
    best = None
    for it in game.items:
        if (it.x, it.y) in claimed:
            continue
        if kinds and not any(k in it.kind for k in kinds):
            continue
        prio = max([v for k, v in ITEM_PRIORITY.items() if k in it.kind] or [2])
        for dx in range(-s + 1, s):
            for dy in range(-s + 1, s):
                a = (it.x + dx, it.y + dy)
                if a in dist and dist[a] <= max_cost:
                    score = dist[a] - 6 * prio
                    if best is None or score < best[0]:
                        best = (score, a, it)
    if not best:
        return None
    _, anchor, it = best
    claimed.add((it.x, it.y))
    if tank.pos == anchor:
        return None
    return go_to(game, tank, anchor, prev, f"item:{it.kind}@{it.x},{it.y}")


def unstick(game, tank):
    """Fallback when a tank keeps getting rejected: one free cell in any direction."""
    for d in DIRS:
        dx, dy = DELTA[d]
        ec = game.grid.enter_cost(tank.x + dx, tank.y + dy, game.tank_size, star3(tank))
        if ec and not ec[1]:
            return Decision(job=f"unstick:{d}", steps=[{"do": "move", "direction": d, "cells": 1}])
    return Decision(job="unstick:fire", steps=[{"do": "move_fire", "direction": tank.dir or "up", "cells": 1}])


# ======================================================================
# BASE
# ======================================================================
ROLE_HELP = {
    "siege": "chiếm vị trí bắn đại bàng địch ở nửa sân địch, bắn LẦN LƯỢT với đồng đội (cách >5s)",
    "siege_left / siege_right": "như siege nhưng đánh từ hông trái / hông phải đại bàng (nhìn theo bản đồ)",
    "siege_side": "như siege, hông nào gần thì đánh hông đó",
    "siege_front": "như siege, bắn theo cột (chính diện)",
    "rush": "như siege nhưng không xếp lượt, ai sẵn sàng thì bắn",
    "stage": "chỉ chiếm vị trí bắn đại bàng, không bắn (dùng khi còn giáp)",
    "hunt": "săn xe địch gần nhất",
    "defend": "chặn xe địch gần nhà, không có thì giữ chốt tốt nhất quanh nhà",
    "guard_front:N": "đứng chốt trước căn cứ ta N ô (mặc định 3), nòng hướng về phía địch, địch lao về thì ra chặn",
    "collect": "nhặt vật phẩm (⭐ trước)",
    "hold": "đứng yên (vẫn bắn cơ hội)",
    "<vai>+collect": "ghé nhặt vật phẩm gần đường (chi phí ≤ 15) rồi làm <vai>",
}


class Strategy:
    """Base class. Override `roles()` for role-based strategies, or `decide()` for full control.

    Every tank first runs reflex(): shoot an aligned enemy, lead-shoot a crossing one, or dodge
    unless the role is `stage`. During armor, siege-type roles stage instead, except 3-star tanks
    (they can break the armor steel).
    """
    name = "base"
    summary = ""
    params_help = {}

    def __init__(self, **params):
        self.params = params

    def p(self, key, default):
        v = self.params.get(key, default)
        if isinstance(default, bool) and isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        if isinstance(default, (int, float)) and isinstance(v, str):
            return type(default)(v)
        if isinstance(default, list) and isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    # --- override one of these
    def roles(self, game, memory):
        return ["hunt"] * len(game.my_tanks)

    def decide(self, game, memory):
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        all_ids = sorted((t.id for t in game.my_tanks), key=str)  # role i belongs to tank #i even if others are dead
        roles = self.roles(game, memory)
        ctx = new_ctx()
        out = {}
        for tank in tanks:
            i = all_ids.index(tank.id)
            role = roles[i % len(roles)] if roles else "hunt"
            out[tank.id] = self.run_role(role, game, tank, memory, ctx)
        return out

    def run_role(self, role, game, tank, memory, ctx):
        return run_role(role, game, tank, memory, ctx, free_fire=self.p("free_fire", True),
                        fire_bricks=self.p("fire_bricks", 0), item_max_cost=self.p("item_max_cost", 40),
                        fire_range=self.p("fire_range", 18))


def new_ctx():
    return {"taken": [], "claimed": set(), "defend_idx": 0}


def run_role(role, game, tank, memory, ctx, free_fire=True, fire_bricks=0, item_max_cost=40, fire_range=18):
    role = (role or "hunt").strip()
    detour = role.endswith("+collect")
    base = role[: -len("+collect")] if detour else role
    name, _, arg = base.partition(":")

    if free_fire:
        shot = reflex(game, tank, memory, max_bricks=fire_bricks, max_range=fire_range,
                      allow_base=(name == "rush"))  # siege shoots the eagle only in rotation
        if shot:
            shot.job = f"{role}|{shot.job}"
            return shot
    if detour:
        dec = collect(game, tank, memory, max_cost=15, claimed=ctx["claimed"])
        if dec:
            dec.job = f"{role}|{dec.job}"
            return dec

    dec = None
    armored = game.armor_active and not star3(tank)
    if name.startswith("siege") or name == "rush":
        prefer = {"siege_left": "left", "siege_right": "right", "siege_side": "side", "siege_front": "front"}.get(name)
        if armored:
            dec = stage(game, tank, memory, ctx["taken"], prefer=prefer)
        else:
            dec = attack_base(game, tank, memory, coordinate=(name != "rush"), taken=ctx["taken"], prefer=prefer)
    elif name == "stage":
        dec = stage(game, tank, memory, ctx["taken"])
    elif name == "hunt":
        dec = hunt(game, tank, memory)
    elif name == "defend":
        dec = defend(game, tank, memory, post_index=ctx["defend_idx"])
        ctx["defend_idx"] += 1
    elif name == "guard_front":
        dec = guard_front(game, tank, memory, n=int(arg or 3))
    elif name == "collect":
        dec = collect(game, tank, memory, max_cost=item_max_cost, claimed=ctx["claimed"])
    elif name == "hold":
        dec = hold()
    if dec is None and name not in ("hunt", "hold", "guard_front", "defend"):
        dec = hunt(game, tank, memory)
    if dec is not None:
        dec.note = dec.note or role
        dec.job = f"{role}|{dec.job}"
    return dec or hold(f"{role}|idle", "không có mục tiêu")


# ======================================================================
# ADAPTIVE
# ======================================================================
@register
class Adaptive(Strategy):
    name = "adaptive"
    summary = ("Đổi vai theo tình huống (chiến thuật mặc định): đang giáp → nhặt đồ + chiếm vị trí bắn + săn; "
               "hết giáp → vây đại bàng có xếp lượt; căn cứ ta yếu/địch tràn sang → thêm thủ; "
               "đại bàng địch sắp vỡ hoặc đang thua gần hết giờ → dồn hết vào vây; dẫn điểm gần hết giờ → co về thủ.")
    params_help = {"lead_margin": "dẫn bao nhiêu điểm thì co về thủ khi gần hết giờ (mặc định 1500)",
                   "endgame_s": "giây game còn lại coi là cuối trận (mặc định 90)"}

    def phase(self, game, memory):
        n = max(1, len(game.alive_tanks(True)))
        my_hp, en_hp = game.my_base_hp, game.enemy_base_hp
        lead = (game.my_score or 0) - (game.enemy_score or 0)
        t = game.time_left
        endgame = t is not None and t <= self.p("endgame_s", 90)
        invaders = sum(1 for e in game.alive_tanks(False) if game.in_my_half(e.y))

        if game.armor_active:
            return "armor", (["collect", "stage", "hunt", "defend"] if n >= 4 else ["collect", "stage", "defend"])[:n] or ["hunt"]
        if en_hp is not None and en_hp <= 2:
            return "finish_eagle", ["siege"] * n
        if endgame and lead >= self.p("lead_margin", 1500):
            return "protect_lead", ["defend"] * (n - 1) + ["hunt"]
        if endgame and lead < 0:
            return "all_in", ["siege"] * n
        if (my_hp is not None and my_hp <= 2) or invaders >= 2:
            return "defensive", ["siege", "siege", "defend", "defend"][:n] if n >= 3 else ["defend", "siege"][:n]
        return "siege", (["siege", "siege", "siege", "defend"] if n >= 4 else ["siege"] * (n - 1) + ["defend"])[:n]

    def decide(self, game, memory):
        phase, roles = self.phase(game, memory)
        memory["adaptive_phase"] = phase
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        ctx = new_ctx()
        out = {}
        for i, tank in enumerate(tanks):
            role = roles[i % len(roles)]
            if phase == "armor" and star3(tank) and role != "defend":
                role = "siege"  # 3 stars break the armor steel: the eagle is open to this tank
            if role == "collect":
                shot = reflex(game, tank, memory)
                dec = shot or collect(game, tank, memory, claimed=ctx["claimed"])
                if dec:
                    dec.job = f"{phase}:collect|{dec.job}"
                    out[tank.id] = dec
                    continue
                role = "stage"
            dec = self.run_role(role, game, tank, memory, ctx)
            dec.job = f"{phase}:{dec.job}"
            out[tank.id] = dec
        return out


# ======================================================================
# SCRIPTED
# ======================================================================
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(cond, game, memory, tank):
    lead = (game.my_score or 0) - (game.enemy_score or 0)
    values = {
        "my_base_hp": game.my_base_hp, "enemy_base_hp": game.enemy_base_hp, "time_left": game.time_left,
        "lead": lead, "invaders": sum(1 for e in game.alive_tanks(False) if game.in_my_half(e.y)),
        "enemy_alive": len(game.alive_tanks(False)),
        "tank_lives": tank.lives if isinstance(tank.lives, (int, float)) else None, "tank_stars": tank.stars,
    }
    for key, want in cond.items():
        if key == "armor":
            if bool(game.armor_active) != bool(want):
                return False
            continue
        if key == "my_base_hit":
            if (game.now - memory.get("my_base_hit", -1e9) < 8) != bool(want):
                return False
            continue
        field, _, op = key.rpartition("_")
        v, w = values.get(field), _num(want)
        if field not in values or op not in ("lte", "gte", "lt", "gt", "eq"):
            print(f"! plan: điều kiện không hiểu '{key}'", file=sys.stderr)
            return False
        if v is None or w is None:
            return False
        if not {"lte": v <= w, "gte": v >= w, "lt": v < w, "gt": v > w, "eq": v == w}[op]:
            return False
    return True


@register
class Scripted(Strategy):
    name = "scripted"
    summary = ("Chiến thuật theo file kế hoạch JSON (vai từng xe + luật đổi vai theo giáp/máu nhà/điểm/thời gian/mạng). "
               "Dùng để làm đúng chiến thuật người dùng viết trong prompt. Sửa file giữa trận là có hiệu lực ngay.")
    params_help = {"plan": "đường dẫn file JSON (mặc định bco-data/<code>/plan.json)"}

    def __init__(self, **params):
        super().__init__(**params)
        self._mtime, self.plan = None, {"roles": {"*": "hunt"}, "rules": []}
        self._load()

    def _load(self):
        path = self.params.get("plan")
        if not path or not os.path.exists(path):
            return
        mt = os.path.getmtime(path)
        if mt == self._mtime:
            return
        self._mtime = mt
        try:
            plan = json.load(open(path, encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"! plan.json lỗi JSON, giữ kế hoạch cũ: {e}", file=sys.stderr)
            return
        known = {r.split(" ")[0].split("/")[0].split(":")[0] for r in ROLE_HELP} | {"siege_left", "siege_right"}
        for scope in [plan.get("roles", {})] + [r.get("roles", {}) for r in plan.get("rules", [])]:
            for tid, role in scope.items():
                base = role.removesuffix("+collect").split(":")[0]
                if base not in known:
                    print(f"! plan: vai lạ '{role}' cho xe {tid}", file=sys.stderr)
        self.plan = plan
        print(f"# nạp kế hoạch {path}", file=sys.stderr)

    def role_for(self, tank, game, memory):
        key = str(tank.id)
        roles = self.plan.get("roles", {})
        role = roles.get(key, roles.get("*", "hunt"))
        for rule in self.plan.get("rules", []):
            if check(rule.get("when", {}), game, memory, tank):
                rr = rule.get("roles", {})
                role = rr.get(key, rr.get("*", role))
        return role

    def decide(self, game, memory):
        self._load()
        ctx, out = new_ctx(), {}
        for tank in sorted(game.alive_tanks(True), key=lambda t: str(t.id)):
            out[tank.id] = self.run_role(self.role_for(tank, game, memory), game, tank, memory, ctx)
        return out


# ======================================================================
# SIEGE
# ======================================================================
@register
class Siege(Strategy):
    name = "siege"
    summary = ("Vây đại bàng: các xe tấn công chiếm các vị trí bắn KHÁC NHAU ở nửa sân địch và bắn LẦN LƯỢT, "
               "cách nhau > 5 giây (đại bàng bất tử 5s, xe trúng đại bàng bị đưa về nhà) để không phí lượt. "
               "5 phát trúng = thắng.")
    params_help = {"defenders": "số xe ở nhà phòng thủ (mặc định 1)"}

    def roles(self, game, memory):
        n = max(1, len(game.alive_tanks(True)))
        d = min(self.p("defenders", 1), n - 1)
        return ["siege"] * (n - d) + ["defend"] * d


# ======================================================================
# RUSH
# ======================================================================
@register
class Rush(Strategy):
    name = "rush"
    summary = ("Cả đội lao vào đại bàng địch, ai vào vị trí trước bắn trước (không xếp lượt). "
               "Trong thời gian giáp: chiếm sẵn vị trí bắn. Mạnh khi địch phòng thủ yếu, tốn mạng.")
    params_help = {"free_fire": "bắn xe địch nằm trên đường (mặc định true)"}

    def roles(self, game, memory):
        return ["rush"]


# ======================================================================
# HUNTER
# ======================================================================
@register
class Hunter(Strategy):
    name = "hunter"
    summary = ("Săn xe: mỗi xe nhận một xe địch khác nhau (gần nhất, ưu tiên xe không có khiên), tìm ô thẳng hàng "
               "để bắn. 500 điểm/xe hạ; địch hết mạng toàn bộ là thắng. Hợp khi địch dàn trải.")
    params_help = {"pair": "true = 2 xe cùng săn 1 mục tiêu (mặc định false)"}

    def decide(self, game, memory):
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        enemies = [e for e in game.alive_tanks(False) if not e.shield] or game.alive_tanks(False)
        out, taken = {}, {}
        cap = 2 if self.p("pair", False) else 1
        for tank in tanks:
            shot = reflex(game, tank, memory, max_bricks=self.p("fire_bricks", 0))
            if shot:
                shot.job = "hunter|" + shot.job
                out[tank.id] = shot
                continue
            pool = [e for e in enemies if taken.get(e.id, 0) < cap] or enemies
            if not pool:
                out[tank.id] = hold("hunter|idle")
                continue
            target = min(pool, key=lambda e: abs(e.x - tank.x) + abs(e.y - tank.y))
            taken[target.id] = taken.get(target.id, 0) + 1
            dec = hunt(game, tank, memory, target) or hunt(game, tank, memory)
            if dec:
                dec.job = "hunter|" + dec.job
            out[tank.id] = dec or hold("hunter|no_lane")
        return out


# ======================================================================
# FORTRESS
# ======================================================================
@register
class Fortress(Strategy):
    name = "fortress"
    summary = ("Phòng thủ: xe giữ các chốt quanh đại bàng ta (chốt có tầm bắn rộng nhất), chặn địch vào gần, "
               "1 xe săn. Dùng khi đang dẫn điểm gần hết giờ hoặc căn cứ ta còn ít máu.")
    params_help = {"hunters": "số xe đi săn (mặc định 1)", "fire_bricks": "bắn xuyên tối đa bao nhiêu lớp gạch (mặc định 1)"}

    def __init__(self, **params):
        params.setdefault("fire_bricks", 1)
        super().__init__(**params)

    def roles(self, game, memory):
        n = max(1, len(game.alive_tanks(True)))
        h = min(self.p("hunters", 1), n)
        return ["defend"] * (n - h) + ["hunt"] * h


# ======================================================================
# COLLECTOR
# ======================================================================
@register
class Collector(Strategy):
    name = "collector"
    summary = ("Ưu tiên vật phẩm (⭐ > ⛏️/🛡️ > ⛑️; 200 điểm/món): xe nào tới gần được thì đi nhặt, "
               "mỗi món một xe; xe còn lại theo vai dự phòng. ⭐3 cho bắn thủng thép — mở thêm đường vào đại bàng.")
    params_help = {"item_max_cost": "chi phí đường tối đa để đi nhặt (mặc định 40)",
                   "fallback": "vai dự phòng theo thứ tự xe (mặc định siege,siege,hunt,defend)"}

    def decide(self, game, memory):
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        fallback = self.p("fallback", ["siege", "siege", "hunt", "defend"])
        ctx = new_ctx()
        out = {}
        for i, tank in enumerate(tanks):
            shot = reflex(game, tank, memory)
            if shot:
                shot.job = "collect|" + shot.job
                out[tank.id] = shot
                continue
            dec = collect(game, tank, memory, max_cost=self.p("item_max_cost", 40), claimed=ctx["claimed"])
            if dec:
                dec.job = "collect|" + dec.job
                out[tank.id] = dec
            else:
                out[tank.id] = self.run_role(fallback[i % len(fallback)], game, tank, memory, ctx)
        return out


# ======================================================================
# BALANCED
# ======================================================================
@register
class Balanced(Strategy):
    name = "balanced"
    summary = ("Chia vai cố định theo thứ tự xe: mặc định 2 vây đại bàng (siege), 1 săn, 1 thủ. "
               "Đổi vai bằng --param roles=siege,hunt,collect,defend.")
    params_help = {"roles": "danh sách vai theo thứ tự xe: siege|rush|stage|hunt|defend|collect|hold"}

    def roles(self, game, memory):
        return self.p("roles", ["siege", "siege", "hunt", "defend"])


# ======================================================================
# RUNNER
# ======================================================================
MAX_QUEUE = 5


class Commander:
    def __init__(self, client, code, strategy, dry_run=False, refresh_s=4.0, min_fire_gap=0.6, log_path=None, echo=print, inline_stop=True):
        self.client, self.code, self.strategy = client, code, strategy
        self.dry_run, self.refresh_s, self.min_fire_gap = dry_run, refresh_s, min_fire_gap
        self.inline_stop = inline_stop
        self.last_response = None
        self.memory = {}          # team memory shared by strategies (eagle timing, caches...)
        self.tanks = {}           # per tank: job, sent_at, fire_at, rejects, pos
        self.prev = None          # previous Game
        self.terrain = {}         # (x, y) -> last terrain seen there (to see under power-ups)
        self.log_path = log_path
        self.echo = echo

    # ------------------------------------------------------------ observation
    def track_enemies(self, game):
        """Estimate enemy velocity (cells/s) by matching each enemy to the nearest one last cycle
        (ids from the ASCII map are not stable). Smoothed; reset on teleport/respawn."""
        prev, vel = self.prev, {}
        old = self.memory.get("enemy_vel") or {}
        if prev:
            dt = game.now - prev.now
            pool = list(prev.enemy_tanks)
            for e in game.enemy_tanks:
                if not pool or dt <= 0 or dt > 3:
                    break
                p = min(pool, key=lambda q: abs(q.x - e.x) + abs(q.y - e.y))
                if abs(p.x - e.x) + abs(p.y - e.y) > 6:
                    continue
                pool.remove(p)
                vx, vy = (e.x - p.x) / dt, (e.y - p.y) / dt
                ox, oy = old.get(p.id, (0.0, 0.0))
                vel[e.id] = (0.6 * vx + 0.4 * ox, 0.6 * vy + 0.4 * oy)
        self.memory["enemy_vel"] = vel

    def remember_terrain(self, game):
        """Power-ups (P) hide the cell below them. Fill those cells with the terrain we saw there in
        earlier cycles (usually brick → the path uses move_fire); record every visible cell."""
        g = game.grid
        for y, row in enumerate(g.rows):
            for x, ch in enumerate(row):
                if ch in OVERLAY_CHARS:
                    if g.cells[y][x] == "item_cell" and (x, y) in self.terrain:
                        g.cells[y][x] = self.terrain[(x, y)]
                elif g.cells[y][x] != "eagle":
                    self.terrain[(x, y)] = g.cells[y][x]

    def observe(self, game):
        self.remember_terrain(game)
        self.track_enemies(game)
        self.memory["fire_gap"] = self.min_fire_gap
        prev = self.prev
        if prev and prev.my_base_hp is not None and game.my_base_hp is not None and game.my_base_hp < prev.my_base_hp:
            self.memory["my_base_hit"] = game.now
        if prev and prev.enemy_base_hp is not None and game.enemy_base_hp is not None and game.enemy_base_hp < prev.enemy_base_hp:
            self.memory["last_eagle_hit"] = game.now
        for ev in game.events:
            et, tid = event_type(ev, game.schema), event_tank(ev, game.schema)
            if ("eagle" in et or "base" in et) and ("hit" in et or "damage" in et):
                self.memory["last_eagle_hit"] = game.now
            if tid is not None and tid in self.tanks and any(k in et for k in ("fail", "reject", "destroy", "kill", "respawn", "died")):
                self.tanks[tid]["job"] = None
        for t in game.my_tanks:
            tm = self.tanks.setdefault(t.id, {"job": None, "sent_at": -1e9, "fire_at": -1e9, "rejects": 0, "pos": t.pos})
            home_row = game.my_base[1]
            jumped = abs(t.pos[0] - tm["pos"][0]) + abs(t.pos[1] - tm["pos"][1]) > 6 and abs(t.y - home_row) <= 2
            if jumped:  # sent home (eagle hit) or respawned: the server dropped its job
                tm["job"] = None
            tm["pos"] = t.pos
        self.prev = game

    # ------------------------------------------------------------ one cycle
    def step(self, game):
        """One cycle: decide, then ONE POST /plan for all tanks.

        Changing a tank's job puts {"tank": n, "do": "stop"} first in its steps (the server
        appends new orders to the queue; max 5 queued). Separate /stop calls are only used
        when inline_stop=False.
        """
        self.observe(game)
        decisions = self.strategy.decide(game, self.memory)
        steps, stopped, report = [], [], {}
        for tid, dec in decisions.items():
            tank, tm = game.tank(tid), self.tanks.get(tid)
            if dec is None or tank is None or tm is None:
                continue
            if tm["rejects"] >= 3:
                dec, tm["rejects"] = unstick(game, tank), 0
            changed = dec.job != tm["job"]
            idle = tank.busy is False
            stale = game.now - tm["sent_at"] > self.refresh_s
            had_plan = tank.busy or (tank.busy is None and tm["sent_at"] > -1e9)
            send, out = False, []
            fires = any(st.get("do") == "fire" for st in dec.steps)
            if dec.urgent:
                send = not fires or game.now - tm["fire_at"] >= self.min_fire_gap
            elif dec.steps and (changed or idle or stale):
                send = True
            elif not dec.steps and changed and tm["job"] and had_plan:
                out = [{"tank": tid, "do": "stop"}]   # switching to "hold": clear the old plan
                stopped.append(tid)
                tm["job"] = dec.job
            if send:
                if had_plan:
                    out.append({"tank": tid, "do": "stop"})
                    stopped.append(tid)
                budget = MAX_QUEUE
                for st in dec.steps:
                    if budget <= 0:
                        break
                    out.append({"tank": tid, **st})
                    budget -= 1
                tm.update(job=dec.job, sent_at=game.now)
                if fires:
                    tm["fire_at"] = game.now
                    self.memory.setdefault("fire_at", {})[tid] = game.now
            steps += out
            report[tid] = {"job": dec.job, "sent": send, "steps": [o for o in out if o["do"] != "stop"], "note": dec.note}

        rejected = {}
        if steps and not self.dry_run:
            if not self.inline_stop:
                for tid in sorted({o["tank"] for o in steps if o["do"] == "stop"}, key=str):
                    try:
                        self.client.stop(self.code, tid)
                    except ApiError as e:
                        report.setdefault(tid, {})["stop_error"] = str(e.body)
                steps = [o for o in steps if o["do"] != "stop"]
            if steps:
                rejected = self._send(steps)
        self._log(game, report, stopped, rejected)
        return report, rejected

    def _send(self, steps):
        sent = sorted({s["tank"] for s in steps}, key=str)
        try:
            resp = self.client.plan(self.code, steps)
        except ApiError as e:
            resp = e.body
        self.last_response = resp
        rejected = find_rejections(resp, sent) if resp else {}
        # The server rejects only the faulty tank: resend that tank alone, stop + first real step.
        for tid in list(rejected):
            tm = self.tanks.get(tid, {})
            mine = [s for s in steps if s["tank"] == tid]
            retry = [s for s in mine if s["do"] == "stop"][:1] + [s for s in mine if s["do"] != "stop"][:1]
            ok = False
            if retry:
                try:
                    ok = not find_rejections(self.client.plan(self.code, retry), [tid])
                except ApiError as e:
                    rejected[tid] = e.body
            if ok:
                rejected.pop(tid)
            else:
                tm["job"] = None
                tm["rejects"] = tm.get("rejects", 0) + 1
        for tid in sent:
            if tid not in rejected and tid in self.tanks:
                self.tanks[tid]["rejects"] = 0
        return rejected

    def _log(self, game, report, stopped, rejected):
        if not self.log_path:
            return
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        rec = {
            "t": time.time(), "status": game.status, "time_left": game.time_left, "armor": game.armor_active,
            "score": [game.my_score, game.enemy_score], "base_hp": [game.my_base_hp, game.enemy_base_hp],
            "phase": self.memory.get("adaptive_phase"), "strategy": self.strategy.name,
            "tanks": {str(t.id): [t.x, t.y, t.dir] for t in game.my_tanks},
            "enemies": {str(t.id): [t.x, t.y] for t in game.enemy_tanks},
            "decisions": {str(k): v for k, v in report.items()}, "stopped": stopped,
            "rejected": {str(k): v for k, v in rejected.items()}, "warnings": game.warnings,
        }
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def summary_line(game, report, rejected, memory):
    parts = [f"[{game.status or '?'}] t={game.time_left} armor={'Y' if game.armor_active else 'N'}",
             f"score {game.my_score}-{game.enemy_score}", f"base {game.my_base_hp}-{game.enemy_base_hp}"]
    if memory.get("adaptive_phase"):
        parts.append(f"phase={memory['adaptive_phase']}")
    for tid, r in sorted(report.items(), key=lambda kv: str(kv[0])):
        flag = "→" if r.get("sent") else "·"
        parts.append(f"T{tid}{flag}{r.get('job', '')}")
    if rejected:
        parts.append(f"REJECTED {rejected}")
    return " | ".join(parts)
