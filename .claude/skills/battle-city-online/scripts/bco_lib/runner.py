"""Commander: turns strategy decisions into /stop + /plan calls with minimal churn."""
import json
import os
import time

from .client import ApiError, find_rejections
from .state import event_tank, event_type
from . import tactics as T

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
        self.log_path = log_path
        self.echo = echo

    # ------------------------------------------------------------ observation
    def observe(self, game):
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
                dec, tm["rejects"] = T.unstick(game, tank), 0
            changed = dec.job != tm["job"]
            idle = tank.busy is False
            stale = game.now - tm["sent_at"] > self.refresh_s
            had_plan = tank.busy or (tank.busy is None and tm["sent_at"] > -1e9)
            send, out = False, []
            if dec.urgent:
                send = game.now - tm["fire_at"] >= self.min_fire_gap
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
                if dec.urgent:
                    tm["fire_at"] = game.now
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
