from bco_lib import tactics as T
from strategies import register
from strategies.base import Strategy


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
            shot = T.opportunistic_fire(game, tank, memory, max_bricks=self.p("fire_bricks", 0), allow_base=False)
            if shot:
                shot.job = "hunter|" + shot.job
                out[tank.id] = shot
                continue
            pool = [e for e in enemies if taken.get(e.id, 0) < cap] or enemies
            if not pool:
                out[tank.id] = T.hold("hunter|idle")
                continue
            target = min(pool, key=lambda e: abs(e.x - tank.x) + abs(e.y - tank.y))
            taken[target.id] = taken.get(target.id, 0) + 1
            dec = T.hunt(game, tank, memory, target) or T.hunt(game, tank, memory)
            if dec:
                dec.job = "hunter|" + dec.job
            out[tank.id] = dec or T.hold("hunter|no_lane")
        return out
