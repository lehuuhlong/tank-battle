from strategies import register
from strategies.base import Strategy


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
