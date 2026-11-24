"""Terrain grid, path finding (for 2x2 tanks) and bullet tracing.

Coordinates: x = column, y = row, y=0 is the top row. A tank/eagle at (x, y) occupies
x..x+s-1 / y..y+s-1 (s = tank_size, default 2; anchor = top-left cell).
"""
import heapq

DELTA = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
DIRS = ("up", "right", "down", "left")

PASSABLE = {"empty", "tree", "ice"}
BULLET_THROUGH = {"empty", "tree", "ice", "water", "eagle"}


class Grid:
    def __init__(self, rows, legend=None, unknown_as="empty"):
        legend = legend or {".": "empty", "#": "brick", "@": "steel", "%": "tree", "~": "water", "-": "ice", " ": "empty"}
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
            if k == "brick" or (k == "steel" and star3):
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
        if "brick" in kinds or "steel" in kinds:
            bricks += 1
    return {"kind": "none", "obj": None, "anchor": None, "bricks": bricks, "dist": max_range}


def render(grid, overlays=None):
    """ASCII render with overlays {(x, y): char} (anchor cells)."""
    rows = [list(r) for r in grid.rows]
    for (x, y), ch in (overlays or {}).items():
        if 0 <= y < grid.H and 0 <= x < grid.W:
            rows[y][x] = ch
    return "\n".join("".join(r) for r in rows)
