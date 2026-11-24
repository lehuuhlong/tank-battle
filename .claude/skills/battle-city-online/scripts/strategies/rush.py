from strategies import register
from strategies.base import Strategy


@register
class Rush(Strategy):
    name = "rush"
    summary = ("Cả đội lao vào đại bàng địch, ai vào vị trí trước bắn trước (không xếp lượt). "
               "Trong thời gian giáp: chiếm sẵn vị trí bắn. Mạnh khi địch phòng thủ yếu, tốn mạng.")
    params_help = {"free_fire": "bắn xe địch nằm trên đường (mặc định true)"}

    def roles(self, game, memory):
        return ["rush"]
