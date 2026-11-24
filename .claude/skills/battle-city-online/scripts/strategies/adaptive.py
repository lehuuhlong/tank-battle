from bco_lib import tactics as T
from strategies import register
from strategies.base import Strategy, new_ctx


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
            if phase == "armor" and T.star3(tank) and role != "defend":
                role = "siege"  # 3 stars break the armor steel: the eagle is open to this tank
            if role == "collect":
                shot = T.opportunistic_fire(game, tank, memory, allow_base=False)
                dec = shot or T.collect(game, tank, memory, claimed=ctx["claimed"])
                if dec:
                    dec.job = f"{phase}:collect|{dec.job}"
                    out[tank.id] = dec
                    continue
                role = "stage"
            dec = self.run_role(role, game, tank, memory, ctx)
            dec.job = f"{phase}:{dec.job}"
            out[tank.id] = dec
        return out
