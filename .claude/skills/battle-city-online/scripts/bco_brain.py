"""Battle City Online — bộ não: khối chiến thuật (tactics), các chiến thuật (strategies), runner gửi lệnh."""
import json
import re
import os
import sys
import time
from dataclasses import dataclass, field

from bco_core import (DELTA, DIRS, OVERLAY_CHARS, ApiError, direction_between, build_path, dijkstra, event_tank, event_type,
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


def blocked_cell_from(obj):
    """Find the blocked cell in an order_failed event / rejection reason: a dict with cell/blocked/at
    [x, y] or {x, y}, or text like "(12, 7)". None if absent."""
    if isinstance(obj, dict):
        for k in ("blocked_cell", "blocked", "cell", "at", "position", "pos", "where"):
            v = obj.get(k)
            if isinstance(v, (list, tuple)) and len(v) >= 2 and all(isinstance(c, int) for c in v[:2]):
                return (v[0], v[1])
            if isinstance(v, dict) and isinstance(v.get("x"), int) and isinstance(v.get("y"), int):
                return (v["x"], v["y"])
        for v in obj.values():
            if isinstance(v, (dict, str)):
                r = blocked_cell_from(v)
                if r:
                    return r
    elif isinstance(obj, str):
        # real server: the cell is inside the "reason" text, e.g. "blocked by steel at (12, 7)".
        # Accept (12, 7) · [12, 7] · x=12 y=7 · at 12,7 — never "move(up, 3)" (not two numbers).
        for pat in (r"[(\[]\s*(\d+)\s*[,;]\s*(\d+)\s*[)\]]",
                    r"x\s*[=:]\s*(\d+)\D{1,6}y\s*[=:]\s*(\d+)",
                    r"(?:at|tại|cell|ô)\s*(\d+)\s*[,; ]\s*(\d+)"):
            m = re.search(pat, obj, re.I)
            if m:
                return (int(m.group(1)), int(m.group(2)))
    return None


def infer_bullets(game, prev):
    """The real state lists bullets with x, y only. Work out each bullet's direction (and whose it is):
    1) same bullet in the previous state: same row/column, moved ≤ 10 cells → direction of motion;
    2) new bullet: right in front of a tank's barrel → that tank's facing (and side)."""
    if not game.bullets:
        return
    s = game.tank_size
    olds = list(prev.bullets) if prev else []
    tanks = [(t, True) for t in game.alive_tanks(True)] + [(t, False) for t in game.alive_tanks(False)]
    for b in game.bullets:
        if b["dir"] and b["mine"] is not None:
            continue
        best = None
        for o in olds:
            dx, dy = b["x"] - o["x"], b["y"] - o["y"]
            if abs(dx) < 0.6 and 0.2 < abs(dy) <= 10:
                d, dist = ("down" if dy > 0 else "up"), abs(dy)
            elif abs(dy) < 0.6 and 0.2 < abs(dx) <= 10:
                d, dist = ("right" if dx > 0 else "left"), abs(dx)
            else:
                continue
            if o.get("dir") and o["dir"] != d:
                continue
            if best is None or dist < best[0]:
                best = (dist, d, o)
        if best:
            b["dir"] = b["dir"] or best[1]
            if b["mine"] is None:
                b["mine"] = best[2].get("mine")
            continue
        near = None
        for t, mine in tanks:
            if not t.dir:
                continue
            dx, dy = DELTA[t.dir]
            if dy:
                across, ahead = b["x"] - t.x, (b["y"] - (t.y + (s - 1 if dy > 0 else 0))) * dy
            else:
                across, ahead = b["y"] - t.y, (b["x"] - (t.x + (s - 1 if dx > 0 else 0))) * dx
            if -0.6 <= across <= s - 0.4 and -0.5 <= ahead <= 5:
                if near is None or ahead < near[0]:
                    near = (ahead, t.dir, mine)
        if near:
            b["dir"] = b["dir"] or near[1]
            if b["mine"] is None:
                b["mine"] = near[2]


def mark_blocked(memory, cell, now, ttl=8.0):
    if cell:
        memory.setdefault("blocked", {})[tuple(cell)] = now + ttl


def detect_fences(game, memory):
    """Hàng rào đạn: two enemy tanks on the same row (or column), >=4 cells apart, nothing solid
    between, facing each other (or, if directions are unknown, both standing still for ~2s).
    Returns [{"axis": "row"|"col", "posts": (e1, e2), "lane": set(anchors)}]."""
    s, out = game.tank_size, []
    vel = memory.get("enemy_vel") or {}
    seen = memory.setdefault("fence_seen", {})
    alive = game.alive_tanks(False)
    current = set()
    for i, a in enumerate(alive):
        for b in alive[i + 1:]:
            if abs(a.y - b.y) < s and abs(a.x - b.x) >= 4:
                axis, (lo, hi), d_lo = "row", sorted((a, b), key=lambda t: t.x), "right"
            elif abs(a.x - b.x) < s and abs(a.y - b.y) >= 4:
                axis, (lo, hi), d_lo = "col", sorted((a, b), key=lambda t: t.y), "down"
            else:
                continue
            d_hi = {"right": "left", "down": "up"}[d_lo]
            hit = trace(game.grid, lo.pos, d_lo, s, [("post", hi.pos, hi)], False, 60)
            if hit["kind"] != "post" or hit["bricks"]:
                continue
            key = (axis, lo.x // 2, lo.y // 2, hi.x // 2, hi.y // 2)
            if lo.dir and hi.dir:
                facing = lo.dir == d_lo and hi.dir == d_hi
            else:  # map-only state: no directions → both parked in line for a while
                still = all(abs(vel.get(t.id, (0, 0))[0]) + abs(vel.get(t.id, (0, 0))[1]) < 0.3 for t in (lo, hi))
                seen[key] = seen.get(key, 0) + 1 if still else 0
                facing = seen[key] >= 4
            if not facing:
                continue
            current.add(key)
            lane = set()
            if axis == "row":
                for x in range(lo.x - s + 1, hi.x + s):
                    for y in range(min(lo.y, hi.y) - s + 1, max(lo.y, hi.y) + s):
                        lane.add((x, y))
            else:
                for y in range(lo.y - s + 1, hi.y + s):
                    for x in range(min(lo.x, hi.x) - s + 1, max(lo.x, hi.x) + s):
                        lane.add((x, y))
            out.append({"axis": axis, "posts": (lo, hi), "lane": lane})
    for k in list(seen):
        if k not in current and seen[k] == 0:
            seen.pop(k)
    return out


def fence_post(memory, enemy):
    """'row'/'col' if `enemy` is a post of a detected fence, else None."""
    for f in memory.get("fences") or []:
        if any(p.id == enemy.id for p in f["posts"]):
            return f["axis"]
    return None


def penalties_for(game, tank, memory=None):
    """Path costs: avoid other tanks (allies soft, enemies more), enemy firing lanes, cells that just
    blocked a move (order_failed), and fence lanes (unless we are shielded)."""
    s, pen = game.tank_size, {}
    memory = memory if memory is not None else {}
    danger_w = game.schema.get("danger_penalty", 8)
    if danger_w:
        for a, n in danger_lanes(game).items():
            pen[a] = danger_w * n
    # our eagle's wall: never shoot our own protection open with move_fire (only if no other way)
    bx, by = game.my_base
    for cy in range(by - 1, by + s + 1):
        for cx in range(bx - 1, bx + s + 1):
            if game.grid.at(cx, cy) in ("brick", "steel", "item_cell") and not (bx <= cx < bx + s and by <= cy < by + s):
                for dx in range(s):
                    for dy in range(s):
                        a = (cx - dx, cy - dy)
                        pen[a] = pen.get(a, 0) + 80
    if not tank.shield:
        for f in memory.get("fences") or []:
            for a in f["lane"]:
                pen[a] = pen.get(a, 0) + 60
    for (cx, cy), until in list((memory.get("blocked") or {}).items()):
        if until < game.now:
            memory["blocked"].pop((cx, cy), None)
            continue
        for dx in range(s):
            for dy in range(s):
                a = (cx - dx, cy - dy)
                pen[a] = pen.get(a, 0) + 40
    for other, cost in [(t, 6) for t in game.alive_tanks(True) if t.id != tank.id] + [(t, 15) for t in game.alive_tanks(False)]:
        for dx in range(-s + 1, s):
            for dy in range(-s + 1, s):
                a = (other.x + dx, other.y + dy)
                pen[a] = max(pen.get(a, 0), cost)
    return pen


def paths_from(game, tank, memory=None, max_cost=None):
    key = (tank.id, tank.pos, tank.stars, max_cost)
    cache = memory.get("_paths") if memory is not None else None
    if cache is not None and cache.get("game") is game and key in cache:
        return cache[key]
    res = dijkstra(game.grid, tank.pos, game.tank_size, star3(tank), penalties_for(game, tank, memory), max_cost)
    if memory is not None:
        if cache is None or cache.get("game") is not game:
            cache = memory["_paths"] = {"game": game}
        cache[key] = res
    return res


def eagle_vulnerable(game, memory):
    """Can a hit on the enemy eagle count right now? The real state says it directly
    (bases.opponent.invulnerable); otherwise fall back to the 5s timer after our last hit."""
    if game.enemy_base_open is not None:
        return game.enemy_base_open
    inv = game.schema.get("eagle_invulnerable_s", 5.0)
    return game.now - memory.get("last_eagle_hit", -1e9) > inv + 0.4


# ---------------------------------------------------------------- shooting
def fire_step(d, count):
    return {"do": "fire", "direction": d, "count": max(1, min(3, count))}


def shot_at(game, tank, d, max_range=60):
    return trace(game.grid, tank.pos, d, game.tank_size, entities_for(game, tank), star3(tank), max_range)


def own_eagle_in_line(game, pos, d, pierce=False):
    """Would a bullet fired from `pos` towards `d` reach OUR eagle if it misses (target moves away or
    dies)? Bullets hit whatever they meet, own base included → never fire such a shot at a tank."""
    hit = trace(game.grid, pos, d, game.tank_size, [("my_base", game.my_base, None)], pierce, 60)
    return hit["kind"] == "my_base" and hit["bricks"] <= 2


def opportunistic_fire(game, tank, memory, max_bricks=0, max_range=18, allow_base=True):
    """Shoot an enemy tank (or the enemy eagle when it counts) already in a straight lane."""
    best = None
    for d in DIRS:
        hit = shot_at(game, tank, d, max_range)
        if hit["kind"] == "enemy" and hit["bricks"] <= max_bricks and not hit["obj"].shield \
                and not own_eagle_in_line(game, tank.pos, d, star3(tank)):
            score = 100 - hit["dist"]
            if best is None or score > best[0]:
                # ≥2 bullets: head-on bullets cancel each other, the follow-up one decides the duel
                best = (score, d, hit["bricks"] + 2 + (1 if hit["dist"] > 8 else 0), f"bắn xe địch {hit['obj'].id}")
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
            if hit["kind"] == "enemy" and hit["obj"] is e and hit["bricks"] == 0 \
                    and not own_eagle_in_line(game, tank.pos, d, star3(tank)):
                return Decision(job=f"lead:{d}", steps=[fire_step(d, 2)], urgent=True, note=f"bắn đón xe địch {e.id}")
    return None


class _Src:
    def __init__(self, id):
        self.id = id


def threats_to(game, tank, rng=16):
    """What can hit `tank` right now. Returns [(dist, source, dir, in_lane(x, y))] sorted by distance:
    - enemy tanks in a straight lane with nothing solid between, facing it (or ≤6 cells: can turn);
    - bullets already flying at it (state.bullets with a direction, not ours, nothing solid between)."""
    s, out = game.tank_size, []
    for b in game.bullets:
        if b["mine"] is True or not b["dir"]:
            continue
        dx, dy = DELTA[b["dir"]]
        bx, by = b["x"], b["y"]
        if dy:
            if not (tank.x - 0.5 <= bx <= tank.x + s - 0.5):
                continue
            dist = (tank.y - by) * dy
        else:
            if not (tank.y - 0.5 <= by <= tank.y + s - 0.5):
                continue
            dist = (tank.x - bx) * dx
        if dist < -1 or dist > 12:
            continue
        cx, cy, blocked = int(round(bx)), int(round(by)), False
        for _ in range(max(0, int(dist) - 1)):
            cx, cy = cx + dx, cy + dy
            if game.grid.at(cx, cy) in ("brick", "steel", "wall", "item_cell"):
                blocked = True
                break
        if not blocked:
            lane = (lambda x, y, bx=bx: x - 0.5 <= bx <= x + s - 0.5) if dy else (lambda x, y, by=by: y - 0.5 <= by <= y + s - 0.5)
            out.append((max(0.0, dist), _Src("đạn"), b["dir"], lane))
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
            lane = (lambda x, y, e=e: abs(e.x - x) < s) if d in ("up", "down") else (lambda x, y, e=e: abs(e.y - y) < s)
            out.append((dist, e, d, lane))
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
            if any(in_lane(x, y) for _, _, _, in_lane in threats):
                continue  # still in a threatening lane: keep sliding
            score = n + 3 * danger.get((x, y), 0)
            if best is None or score < best[0]:
                best = (score, dd, n)
            break
    if not best:
        return None
    _, dd, n = best
    return Decision(job=f"dodge:{dd}{n}@{tank.x},{tank.y}", steps=[{"do": "move", "direction": dd, "cells": n}],
                    note=f"né {threats[0][1].id}" if isinstance(threats[0][1], _Src) else f"né xe địch {threats[0][1].id}")


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
                 min_dist=1, exclude=(), max_candidates=400, prefer=None, assume_star3=False, only_axis=None):
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
        if only_axis == "col" and not abs(x - tx) < s or only_axis == "row" and not abs(y - ty) < s:
            continue
        if prefer and prefer != spot_side((x, y), d, (tx, ty)) and not (prefer == "side" and d in ("left", "right")):
            continue
        ghost = type(tank)(id=tank.id, x=x, y=y, stars=tank.stars)
        hit = trace(game.grid, (x, y), d, s, entities_for(game, ghost), star3(tank) or assume_star3)
        if hit["kind"] != target_kind or hit["anchor"] != tuple(target_anchor):
            continue
        if hit["bricks"] > max_bricks or hit["dist"] < min_dist:
            continue
        if target_kind == "enemy" and own_eagle_in_line(game, (x, y), d, star3(tank)):
            continue  # a miss would fly on into our own eagle
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
    """Săn xe: pick the enemy whose nearest firing spot is cheapest to reach (real path cost, not
    straight-line distance), go there and fire 2+ bullets on arrival."""
    enemies = [e for e in game.alive_tanks(False) if not e.shield] or game.alive_tanks(False)
    if not enemies:
        return None
    candidates = [target] if target else sorted(enemies, key=lambda e: abs(e.x - tank.x) + abs(e.y - tank.y))[:3]
    best = None
    for e in candidates:
        axis = fence_post(memory, e)  # fence post faces along its row → come at it along its column
        only = {"row": "col", "col": "row"}.get(axis)
        spots, prev = firing_spots(game, tank, e.pos, "enemy", memory, max_bricks=1, min_dist=2, only_axis=only)
        if spots:
            # weaker targets first: fewer lives left is a sooner win for the "all lives lost" condition too
            score = spots[0][0] + (2 if isinstance(e.lives, (int, float)) and e.lives > 5 else 0)
            if best is None or score < best[0]:
                best = (score, e, spots[0], prev)
    if not best:
        return None
    _, target, (_, spot, d, bricks), prev = best
    count = bricks + 2
    if tank.pos == spot:
        return Decision(job=f"hunt_fire:{target.id}", steps=[fire_step(d, count)], urgent=True)
    # target moves: label by target + coarse area so we re-plan when it relocates
    return go_to(game, tank, spot, prev, f"hunt:{target.id}@{target.x // 6},{target.y // 6}",
                 max_steps=3, then=fire_step(d, count))


def break_fence(game, tank, memory, taken_posts=None):
    """Phá rào: hunt the nearest post of a fence (from its blind side). No fence → None."""
    taken_posts = taken_posts if taken_posts is not None else set()
    posts = [p for f in memory.get("fences") or [] for p in f["posts"] if p.id not in taken_posts]
    if not posts:
        return None
    target = min(posts, key=lambda e: abs(e.x - tank.x) + abs(e.y - tank.y))
    taken_posts.add(target.id)
    dec = hunt(game, tank, memory, target)
    if dec:
        dec.job, dec.note = "breaker:" + dec.job, f"phá rào: xe địch {target.id}"
    return dec


# ---------------------------------------------------------------- base defence vs flank attacks
def side_of(game, pos):
    """Hướng của một vị trí so với đại bàng ta: 'left' / 'right' (hai hông) hoặc 'front' (chính diện)."""
    dx, dy = pos[0] - game.my_base[0], pos[1] - game.my_base[1]
    if abs(dx) > abs(dy):
        return "left" if dx < 0 else "right"
    return "front"


def eagle_threats(game, max_bricks=2):
    """Enemy tanks in our half with a straight bullet line to OUR eagle (≤ max_bricks brick layers;
    a tank of ours standing in the lane already blocks it). Most urgent first."""
    s, out = game.tank_size, []
    for e in game.alive_tanks(False):
        if not game.in_my_half(e.y):
            continue  # shots from its own half do no damage
        d = direction_between(e.pos, game.my_base, s)
        if not d:
            continue
        ents = [("my_base", game.my_base, None)] + [("mine", t.pos, t) for t in game.alive_tanks(True)]
        ents += [("other", o.pos, o) for o in game.alive_tanks(False) if o is not e]
        hit = trace(game.grid, e.pos, d, s, ents, star3(e), 60)
        if hit["kind"] == "my_base" and hit["bricks"] <= max_bricks:
            out.append({"enemy": e, "dir": d, "bricks": hit["bricks"], "dist": hit["dist"], "side": side_of(game, e.pos)})
    return sorted(out, key=lambda t: (t["bricks"], t["dist"]))


def block_anchors(game, th):
    """Free anchors inside the bullet lane between the attacker and our eagle (body-block spots),
    closest to the eagle first."""
    s, e, d = game.tank_size, th["enemy"], th["dir"]
    bx, by = game.my_base
    out = []
    if d in ("left", "right"):
        xs = range(e.x + s, bx - s + 1) if d == "right" else range(bx + s, e.x - s + 1)
        for x in xs:
            for y in range(e.y - s + 1, e.y + s):
                out.append((x, y))
    else:
        ys = range(e.y + s, by - s + 1) if d == "down" else range(by + s, e.y - s + 1)
        for y in ys:
            for x in range(e.x - s + 1, e.x + s):
                out.append((x, y))
    free = []
    for a in out:
        ec = game.grid.enter_cost(a[0], a[1], s)
        if ec and not ec[1] and not overlaps(a[0], a[1], bx, by, s):
            free.append(a)
    return sorted(free, key=lambda a: abs(a[0] - bx) + abs(a[1] - by))


def guard_eagle(game, tank, memory, th):
    """Cứu nhà vs one attacker: shoot it now if we can; else go to the cheaper of a firing spot or a
    body-block spot in its lane (a tank in the lane takes the bullet instead of the eagle).
    Returns (cost, Decision) or None."""
    e = th["enemy"]
    d = direction_between(tank.pos, e.pos, game.tank_size)
    if d:
        hit = shot_at(game, tank, d)
        if hit["kind"] == "enemy" and hit["obj"] is e and hit["bricks"] <= 1 and can_fire(game, tank, memory) \
                and not own_eagle_in_line(game, tank.pos, d, star3(tank)):
            return 0, Decision(job=f"save_fire:{e.id}", steps=[fire_step(d, hit["bricks"] + 2)], urgent=True,
                               note=f"cứu nhà: bắn xe địch {e.id}")
    dist, prev = paths_from(game, tank, memory)
    best = None
    spots, _ = firing_spots(game, tank, e.pos, "enemy", memory, max_bricks=0, min_dist=2)
    if spots:
        c, spot, fd, _ = spots[0]
        best = (c, spot, fire_step(fd, 2), "bắn")
    face = {"left": "right", "right": "left", "up": "down", "down": "up"}[th["dir"]]  # look at the attacker
    for a in block_anchors(game, th):
        if a in dist and (best is None or dist[a] - 3 < best[0]):
            best = (dist[a] - 3, a, {"do": "turn", "direction": face}, "chắn")
            break
    if not best:
        return None
    cost, spot, then, how = best
    if tank.pos == spot:
        return cost, Decision(job=f"save_{how}:{e.id}@{spot}", steps=[then], urgent=then["do"] == "fire",
                              note=f"cứu nhà: {how} xe địch {e.id}")
    dec = go_to(game, tank, spot, prev, f"save_{how}:{e.id}@{spot}", max_steps=3, then=then)
    if dec:
        dec.note = f"cứu nhà: {how} xe địch {e.id}"
        return cost, dec
    return None


def eagle_emergency(game, memory, max_cost=25):
    """Highest priority, every strategy: for each enemy with a shot at our eagle, the closest of our
    tanks (path cost ≤ max_cost, so far-away attackers keep attacking) shoots it or blocks its lane."""
    threats = eagle_threats(game)
    memory["eagle_threats"] = [(th["enemy"].id, th["side"]) for th in threats]
    used, out = set(), {}
    for th in threats:
        best = None
        for t in game.alive_tanks(True):
            if t.id in used:
                continue
            r = guard_eagle(game, t, memory, th)
            if r and r[0] <= max_cost and (best is None or r[0] < best[0]):
                best = (r[0], t, r[1])
        if best:
            used.add(best[1].id)
            out[best[1].id] = best[2]
    return out


def flank_post(game, side):
    """Chốt hông: anchor in our eagle's row lane, just outside its wall ring on `side`."""
    s, (bx, by) = game.tank_size, game.my_base
    xs = range(bx - s - 1, bx - s - 9, -1) if side == "left" else range(bx + s + 1, bx + s + 9)
    for x in xs:
        for y in sorted(range(by - s + 1, by + s), key=lambda y: abs(y - by)):
            if 0 <= y <= game.grid.H - s and 0 <= x <= game.grid.W - s:
                ec = game.grid.enter_cost(x, y, s)
                if ec and not ec[1]:
                    return (x, y)
    return None


def guard_side(game, tank, memory, side, claimed=None):
    """guard_left / guard_right: intercept attackers on that side of our eagle, otherwise hold the
    flank post facing outward."""
    claimed = claimed if claimed is not None else set()
    near = [e for e in game.alive_tanks(False) if e.id not in claimed and game.in_my_half(e.y)
            and side_of(game, e.pos) == side and abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]) <= 18]
    if near:
        target = min(near, key=lambda e: abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]))
        claimed.add(target.id)
        dec = hunt(game, tank, memory, target)
        if dec:
            dec.job, dec.note = f"flank_{side}:" + dec.job, f"giữ hông {side}: chặn xe địch {target.id}"
            return dec
    post = flank_post(game, side)
    if not post:
        return defend(game, tank, memory, claimed=claimed)
    if tank.pos == post:
        if tank.dir != side:
            return Decision(job=f"flank_{side}:face", steps=[{"do": "turn", "direction": side}])
        return hold(f"flank_{side}:hold")
    _, prev = paths_from(game, tank, memory)
    return go_to(game, tank, post, prev, f"flank_{side}:go{post}", then={"do": "turn", "direction": side})


def decide_all(strategy, game, memory):
    """Strategy decisions + the eagle emergency override (applies to every strategy)."""
    decisions = strategy.decide(game, memory)
    for tid, dec in eagle_emergency(game, memory).items():
        dec.job = "cứu nhà|" + dec.job
        decisions[tid] = dec
    return decisions


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


def defend(game, tank, memory, post_index=0, intercept_radius=14, post=None, claimed=None):
    # our eagle was just hit (server also reports the shooter): widen the intercept net for 8s
    if game.now - memory.get("my_base_hit", -1e9) < 8:
        intercept_radius = 24
    # intercept enemies close to our eagle first
    claimed = claimed if claimed is not None else set()
    threats = [e for e in game.alive_tanks(False)
               if game.in_my_half(e.y) and abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]) <= intercept_radius]
    # each defender takes a different attacker (two flanks → two defenders, one per side)
    threats = [e for e in threats if e.id not in claimed] or threats
    if threats:
        target = min(threats, key=lambda e: abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]))
        claimed.add(target.id)
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


def unstick(game, tank, memory=None, blocked=None):
    """Thoát kẹt: back off 1-2 free cells AWAY from the blocked cell (if known), preferring cells
    outside enemy lanes; next cycle the path is re-planned around the (penalised) blocked cell."""
    s, danger = game.tank_size, danger_lanes(game)
    others = [t for t in game.alive_tanks(True) if t.id != tank.id] + game.alive_tanks(False)
    best = None
    for d in DIRS:
        dx, dy = DELTA[d]
        away = 0
        if blocked:
            away = -(dx * (blocked[0] - tank.x) + dy * (blocked[1] - tank.y))  # >0: moving away from it
        for n in (2, 1):
            cells = [(tank.x + dx * k, tank.y + dy * k) for k in range(1, n + 1)]
            if all((ec := game.grid.enter_cost(x, y, s, star3(tank))) and not ec[1]
                   and not any(abs(o.x - x) < s and abs(o.y - y) < s for o in others) for x, y in cells):
                x, y = cells[-1]
                score = -2 * (away > 0) + 2 * (away < 0) + danger.get((x, y), 0) - 0.5 * n
                if best is None or score < best[0]:
                    best = (score, d, n)
                break
    if best:
        _, d, n = best
        return Decision(job=f"unstick:{d}{n}", steps=[{"do": "move", "direction": d, "cells": n}], note="thoát kẹt")
    return Decision(job="unstick:fire", steps=[{"do": "move_fire", "direction": tank.dir or "up", "cells": 1}], note="thoát kẹt")


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
    "breaker": "phá hàng rào đạn: săn xe rào gần nhất từ phía nó không quay nòng (không có rào thì như hunt)",
    "defend": "chặn xe địch gần nhà, không có thì giữ chốt tốt nhất quanh nhà",
    "guard_front:N": "đứng chốt trước căn cứ ta N ô (mặc định 3), nòng hướng về phía địch, địch lao về thì ra chặn",
    "guard_left": "giữ hông trái đại bàng ta: chặn xe địch đến từ bên trái, không có thì đứng chốt trong làn hông trái, nòng hướng ra ngoài",
    "guard_right": "giữ hông phải đại bàng ta (như guard_left, bên phải)",
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
    elif name == "breaker":
        dec = break_fence(game, tank, memory, ctx.setdefault("posts", set())) or hunt(game, tank, memory)
    elif name == "defend":
        dec = defend(game, tank, memory, post_index=ctx["defend_idx"], claimed=ctx.setdefault("claimed_enemies", set()))
        ctx["defend_idx"] += 1
    elif name in ("guard_left", "guard_right"):
        dec = guard_side(game, tank, memory, name.split("_")[1], ctx.setdefault("claimed_enemies", set()))
    elif name == "guard_front":
        dec = guard_front(game, tank, memory, n=int(arg or 3))
    elif name == "collect":
        dec = collect(game, tank, memory, max_cost=item_max_cost, claimed=ctx["claimed"])
    elif name == "hold":
        dec = hold()
    if dec is None and name not in ("hunt", "hold", "guard_front", "defend", "guard_left", "guard_right"):
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
            # armor = eagles closed: score with kills (+500) and items (+200, stars), keep one at home;
            # only in the last ~25 game-seconds move attackers to their firing spots
            late = game.armor_left is not None and game.armor_left <= self.p("stage_before_s", 25)
            if late:
                return "armor_end", (["stage", "stage", "hunt+collect", "defend"] if n >= 4 else ["stage", "defend", "hunt"])[:n]
            # the eagle is armored: a home guard only matters once an enemy has 3 stars (breaks the steel)
            guard = any((e.stars or 0) >= 3 for e in game.alive_tanks(False))
            roles = ["hunt+collect", "collect", "hunt+collect", "defend" if guard else "hunt+collect"]
            return "armor", roles[:n] or ["hunt"]
        if en_hp is not None and en_hp <= 2:
            return "finish_eagle", ["siege"] * n
        if endgame and lead >= self.p("lead_margin", 1500):
            return "protect_lead", ["defend"] * (n - 1) + ["hunt"]
        if endgame and lead < 0:
            return "all_in", ["siege"] * n
        sides = {side_of(game, e.pos) for e in game.alive_tanks(False)
                 if game.in_my_half(e.y) and abs(e.x - game.my_base[0]) + abs(e.y - game.my_base[1]) <= 22}
        if {"left", "right"} <= sides:
            # attacked from both flanks: one guard per flank, the rest keep attacking (their base is thinner now)
            if n >= 4:
                return "two_flanks", (["siege", "guard_left", "guard_right", "defend"] if my_hp is not None and my_hp <= 2
                                      else ["siege", "siege", "guard_left", "guard_right"])
            return "two_flanks", ["guard_left", "guard_right", "siege"][:n] if n >= 2 else ["defend"]
        if (my_hp is not None and my_hp <= 2) or invaders >= 2:
            return "defensive", ["siege", "siege", "defend", "defend"][:n] if n >= 3 else ["defend", "siege"][:n]
        return "siege", (["siege", "siege", "siege", "defend"] if n >= 4 else ["siege"] * (n - 1) + ["defend"])[:n]

    def decide(self, game, memory):
        phase, roles = self.phase(game, memory)
        fences = memory.get("fences") or []
        if fences and phase not in ("finish_eagle", "protect_lead"):
            # a fence holds 2 enemy tanks: send 1 breaker per fence (max 2), taken from attack/hunt roles
            need = min(2, len(fences))
            for i in range(len(roles) - 1, -1, -1):
                if need and roles[i] in ("siege", "hunt", "stage", "collect"):
                    roles[i], need = "breaker", need - 1
            phase += "+rào"
        memory["adaptive_phase"] = phase
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        # flank guards go to the tank closest to each flank; the other roles keep tank order
        assign, free = {}, list(tanks)
        for r in [r for r in roles if r.startswith("guard_") and r != "guard_front"]:
            post = flank_post(game, r.split("_")[1]) or game.my_base
            if free:
                t = min(free, key=lambda t: abs(t.x - post[0]) + abs(t.y - post[1]))
                assign[t.id] = r
                free.remove(t)
        rest = [r for r in roles if not (r.startswith("guard_") and r != "guard_front")] or ["hunt"]
        for i, t in enumerate(free):
            assign[t.id] = rest[i % len(rest)]
        ctx = new_ctx()
        out = {}
        for tank in tanks:
            role = assign[tank.id]
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
        known = {r.split(" ")[0].split("/")[0].split(":")[0] for r in ROLE_HELP} | {"siege_left", "siege_right", "guard_left", "guard_right"}
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
        self.pending_window_s = 1.2   # tick 0.5s + network; overridable (used when the state has no "round")
        self.pending_round = {}       # tank -> round at which our last orders apply (from the /plan reply)
        self.last_applies_round = None
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
                    if ch in "P*" and (x, y) in self.terrain:  # power-up / bullet drawn over the real cell
                        g.cells[y][x] = self.terrain[(x, y)]
                elif g.cells[y][x] != "eagle":
                    self.terrain[(x, y)] = g.cells[y][x]

    def observe(self, game):
        self.remember_terrain(game)
        infer_bullets(game, self.prev)
        self.memory["fences"] = detect_fences(game, self.memory)
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
            if "fail" in et or "block" in et:
                cell = blocked_cell_from(ev)
                mark_blocked(self.memory, cell, game.now)
                if tid in self.tanks and cell:
                    self.tanks[tid]["blocked"] = cell
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
        decisions = decide_all(self.strategy, game, self.memory)
        steps, stopped, report = [], [], {}
        for tid, dec in decisions.items():
            tank, tm = game.tank(tid), self.tanks.get(tid)
            if dec is None or tank is None or tm is None:
                continue
            if tm["rejects"] >= 3:
                dec, tm["rejects"] = unstick(game, tank, self.memory, tm.get("blocked")), 0
            changed = dec.job != tm["job"]
            idle = tank.busy is False
            stale = game.now - tm["sent_at"] > self.refresh_s
            # orders apply at the next 0.5s tick, so right after a send the state can still show the tank
            # idle: treat it as busy for a moment, or a new plan would be appended to the pending one
            had_plan = tank.busy or (tank.busy is None and tm["sent_at"] > -1e9) or self.pending(game, tid, tm)
            send, out = False, []
            fires = any(st.get("do") == "fire" for st in dec.steps)
            if dec.urgent:
                send = not fires or game.now - tm["fire_at"] >= self.min_fire_gap
            elif dec.steps and (changed or (idle and not self.pending(game, tid, tm)) or stale):
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

    def pending(self, game, tid, tm):
        """Orders sent but not applied yet? Use rounds when the server gives them, else a time window."""
        if game.round is not None and tid in self.pending_round:
            return game.round < self.pending_round[tid]
        return game.now - tm["sent_at"] < self.pending_window_s

    def _note_rounds(self, resp, sent):
        """Real /plan reply: {"ok", "accepted", "steps": [{"tank", "ok", "steps_waiting", "applies_in_round"?}]},
        sometimes a top-level "applies_in_round"."""
        if not isinstance(resp, dict):
            return
        top = resp.get("applies_in_round")
        per = {}
        for st in resp.get("steps") or []:
            if isinstance(st, dict) and st.get("ok") is not False and st.get("tank") is not None:
                per[st["tank"]] = st.get("applies_in_round", top)
        for tid in sent:
            r = per.get(tid, top)
            if isinstance(r, int):
                self.pending_round[tid] = r
                self.last_applies_round = max(self.last_applies_round or 0, r)

    def _send(self, steps):
        sent = sorted({s["tank"] for s in steps}, key=str)
        try:
            resp = self.client.plan(self.code, steps)
        except ApiError as e:
            resp = e.body
        self.last_response = resp
        self._note_rounds(resp, sent)
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
                cell = blocked_cell_from(rejected.get(tid))
                if cell:
                    mark_blocked(self.memory, cell, time.time() if self.prev is None else self.prev.now)
                    tm["blocked"] = cell
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
