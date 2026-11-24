from strategies import register
from strategies.base import Strategy


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
