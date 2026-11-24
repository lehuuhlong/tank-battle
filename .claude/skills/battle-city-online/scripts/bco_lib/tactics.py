"""Tactical building blocks shared by every strategy.

Each helper returns a Decision (job label + command steps) or None. Strategies combine them.
The runner only re-sends a tank's plan when its job label changes, it goes idle, or the
decision is urgent (a shot) — so labels should name the *goal*, not the path.
"""
from dataclasses import dataclass, field

from .grid import DIRS, DELTA, dijkstra, build_path, path_to_steps, trace, overlaps

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
    s = game.tank_size
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
        if tank.dir != face and not any(e for e in game.alive_tanks(False) if abs(e.x - post[0]) < s_(game) or abs(e.y - post[1]) < s_(game)):
            return Decision(job=f"guard_face:{post}", steps=[{"do": "turn", "direction": face}])
    return dec


def s_(game):
    return game.tank_size


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
