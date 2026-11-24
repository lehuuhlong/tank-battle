"""Chiến thuật viết bằng file kế hoạch JSON — agent dịch chiến thuật trong prompt của người dùng vào đây.

Định dạng (xem references/plan-example.json):
{
  "roles": {"1": "siege_left", "2": "siege_left", "3": "hunt+collect", "4": "guard_front:3", "*": "hunt"},
  "rules": [                                   # xét theo thứ tự, luật sau ghi đè luật trước
    {"when": {"armor": true}, "roles": {"*": "hunt+collect"}},
    {"when": {"my_base_hp_lte": 2}, "roles": {"3": "defend"}},
    {"when": {"time_left_lte": 60, "lead_gte": 1}, "roles": {"*": "defend"}}
  ]
}
Điều kiện chung: armor, my_base_hp_lte/gte, enemy_base_hp_lte/gte, time_left_lte/gte (giây đồng hồ game),
lead_gte/lte (điểm ta - điểm địch), invaders_gte (xe địch đang ở nửa sân ta), my_base_hit (bị bắn trong 8s qua),
enemy_alive_lte. Điều kiện theo từng xe: tank_lives_lte/gte, tank_stars_gte/lte.
File được đọc lại mỗi khi thay đổi — sửa file là đổi chiến thuật ngay giữa trận.
"""
import json
import os
import sys

from strategies import register
from strategies.base import ROLE_HELP, Strategy, new_ctx


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
