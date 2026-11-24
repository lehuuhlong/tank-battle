from strategies import register
from strategies.base import Strategy


@register
class Balanced(Strategy):
    name = "balanced"
    summary = ("Chia vai cố định theo thứ tự xe: mặc định 2 vây đại bàng (siege), 1 săn, 1 thủ. "
               "Đổi vai bằng --param roles=siege,hunt,collect,defend.")
    params_help = {"roles": "danh sách vai theo thứ tự xe: siege|rush|stage|hunt|defend|collect|hold"}

    def roles(self, game, memory):
        return self.p("roles", ["siege", "siege", "hunt", "defend"])
