from bco_lib import tactics as T
from strategies import register
from strategies.base import Strategy, new_ctx


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
            shot = T.opportunistic_fire(game, tank, memory, allow_base=False)
            if shot:
                shot.job = "collect|" + shot.job
                out[tank.id] = shot
                continue
            dec = T.collect(game, tank, memory, max_cost=self.p("item_max_cost", 40), claimed=ctx["claimed"])
            if dec:
                dec.job = "collect|" + dec.job
                out[tank.id] = dec
            else:
                out[tank.id] = self.run_role(fallback[i % len(fallback)], game, tank, memory, ctx)
        return out
