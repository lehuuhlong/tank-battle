from bco_lib import tactics as T

ROLE_HELP = {
    "siege": "chiếm vị trí bắn đại bàng địch ở nửa sân địch, bắn LẦN LƯỢT với đồng đội (cách >5s)",
    "siege_left / siege_right": "như siege nhưng đánh từ hông trái / hông phải đại bàng (nhìn theo bản đồ)",
    "siege_side": "như siege, hông nào gần thì đánh hông đó",
    "siege_front": "như siege, bắn theo cột (chính diện)",
    "rush": "như siege nhưng không xếp lượt, ai sẵn sàng thì bắn",
    "stage": "chỉ chiếm vị trí bắn đại bàng, không bắn (dùng khi còn giáp)",
    "hunt": "săn xe địch gần nhất",
    "defend": "chặn xe địch gần nhà, không có thì giữ chốt tốt nhất quanh nhà",
    "guard_front:N": "đứng chốt trước căn cứ ta N ô (mặc định 3), nòng hướng về phía địch, địch lao về thì ra chặn",
    "collect": "nhặt vật phẩm (⭐ trước)",
    "hold": "đứng yên (vẫn bắn cơ hội)",
    "<vai>+collect": "ghé nhặt vật phẩm gần đường (chi phí ≤ 15) rồi làm <vai>",
}


class Strategy:
    """Base class. Override `roles()` for role-based strategies, or `decide()` for full control.

    Every tank first checks opportunistic_fire (free shots at enemy tanks in a straight lane)
    unless the role is `stage`. During armor, siege-type roles stage instead, except 3-star tanks
    (they can break the armor steel).
    """
    name = "base"
    summary = ""
    params_help = {}

    def __init__(self, **params):
        self.params = params

    def p(self, key, default):
        v = self.params.get(key, default)
        if isinstance(default, bool) and isinstance(v, str):
            return v.lower() in ("1", "true", "yes", "on")
        if isinstance(default, (int, float)) and isinstance(v, str):
            return type(default)(v)
        if isinstance(default, list) and isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    # --- override one of these
    def roles(self, game, memory):
        return ["hunt"] * len(game.my_tanks)

    def decide(self, game, memory):
        tanks = sorted(game.alive_tanks(True), key=lambda t: str(t.id))
        all_ids = sorted((t.id for t in game.my_tanks), key=str)  # role i belongs to tank #i even if others are dead
        roles = self.roles(game, memory)
        ctx = new_ctx()
        out = {}
        for tank in tanks:
            i = all_ids.index(tank.id)
            role = roles[i % len(roles)] if roles else "hunt"
            out[tank.id] = self.run_role(role, game, tank, memory, ctx)
        return out

    def run_role(self, role, game, tank, memory, ctx):
        return run_role(role, game, tank, memory, ctx, free_fire=self.p("free_fire", True),
                        fire_bricks=self.p("fire_bricks", 0), item_max_cost=self.p("item_max_cost", 40),
                        fire_range=self.p("fire_range", 18))


def new_ctx():
    return {"taken": [], "claimed": set(), "defend_idx": 0}


def run_role(role, game, tank, memory, ctx, free_fire=True, fire_bricks=0, item_max_cost=40, fire_range=18):
    role = (role or "hunt").strip()
    detour = role.endswith("+collect")
    base = role[: -len("+collect")] if detour else role
    name, _, arg = base.partition(":")

    if free_fire and name != "stage":
        shot = T.opportunistic_fire(game, tank, memory, max_bricks=fire_bricks, max_range=fire_range,
                                    allow_base=(name == "rush"))  # siege shoots the eagle only in rotation
        if shot:
            shot.job = f"{role}|{shot.job}"
            return shot
    if detour:
        dec = T.collect(game, tank, memory, max_cost=15, claimed=ctx["claimed"])
        if dec:
            dec.job = f"{role}|{dec.job}"
            return dec

    dec = None
    armored = game.armor_active and not T.star3(tank)
    if name.startswith("siege") or name == "rush":
        prefer = {"siege_left": "left", "siege_right": "right", "siege_side": "side", "siege_front": "front"}.get(name)
        if armored:
            dec = T.stage(game, tank, memory, ctx["taken"], prefer=prefer)
        else:
            dec = T.attack_base(game, tank, memory, coordinate=(name != "rush"), taken=ctx["taken"], prefer=prefer)
    elif name == "stage":
        dec = T.stage(game, tank, memory, ctx["taken"])
    elif name == "hunt":
        dec = T.hunt(game, tank, memory)
    elif name == "defend":
        dec = T.defend(game, tank, memory, post_index=ctx["defend_idx"])
        ctx["defend_idx"] += 1
    elif name == "guard_front":
        dec = T.guard_front(game, tank, memory, n=int(arg or 3))
    elif name == "collect":
        dec = T.collect(game, tank, memory, max_cost=item_max_cost, claimed=ctx["claimed"])
    elif name == "hold":
        dec = T.hold()
    if dec is None and name not in ("hunt", "hold", "guard_front", "defend"):
        dec = T.hunt(game, tank, memory)
    if dec is not None:
        dec.note = dec.note or role
        dec.job = f"{role}|{dec.job}"
    return dec or T.hold(f"{role}|idle", "không có mục tiêu")
