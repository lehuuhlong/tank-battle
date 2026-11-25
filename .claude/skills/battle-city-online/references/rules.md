# Luật, API & chiến thuật — Battle City Online

Nguồn: trang hướng dẫn http://tank.recoff.cloud (mục Luật chơi, Chuẩn bị agent). Con số là cấu hình mặc định
của server; **luật của chính trận (`GET /matches/{code}/rules`) luôn thắng tài liệu này**.
Tên từng trường của `state` và từng sự kiện: trang Hướng dẫn trên web, mục **Tài liệu API** — đọc nó khi điền `state-schema.json`.

## Endpoint (đã xác minh trên server thật)
Base `http://tank.recoff.cloud/api/v1` · mọi request: `Authorization: Bearer $BCO_TOKEN` · mã phòng `{code}` 4 ký tự.
Chỉ những route dưới đây tồn tại; mọi route khác trả `404 {"error":"not found"}`.

| Gọi | Mục đích |
|---|---|
| `GET /matches` | Trận của bạn; `playing: true` là trận cần chơi |
| `GET /matches/{code}` | Bạn ở bên trên/dưới, cấu hình trận. Gửi `X-Agent-Model: <model>`. **Gọi sớm**: nút Sẵn sàng chỉ bấm được khi agent đã gọi API cho phòng |
| `GET /matches/{code}/rules` | Luật trận này (đọc một lần) |
| `GET /matches/{code}/state?map=true` | Trạng thái, sự kiện mới, bản đồ ASCII |
| `POST /matches/{code}/plan` | `{"steps":[{"tank":1,"do":"..."},...]}` nhiều lệnh một lần |
| `POST /matches/{code}/orders` | `{"tank":N,"do":"..."}` một lệnh |
| `POST /matches/{code}/stop` | `{"tank":N}` xoá kế hoạch của xe |

Lệnh `do`: `move` / `move_fire` (`direction`, `cells`) · `turn` (`direction`) · `fire` (`count` 1-3, `direction`) · `stop`.
**`fire` nhận `direction`**: bắn hướng nào cũng được mà **không cần `turn` trước** (không mất nhịp). Lệnh mới nối cuối kế hoạch;
đổi việc thì đặt `{"tank":n,"do":"stop"}` lên đầu. Tối đa 5 bước chờ. **Khoá thừa bị từ chối** (`move does not take foo`).

Phản hồi:
```
/plan   {"ok":true,"accepted":2,"steps":[{"tank":1,"do":"move","ok":true,"steps_waiting":1}, ...]}
/orders {"tank":1,"do":"move","ok":true,"steps_waiting":2,"max_steps":5,"applies_in_round":77}
/stop   {"ok":true,"queued":true,"tank":3,"order":null,"plan":[],"applies_in_s":0.6,"applies_in_round":91}
```
Lỗi: `401 missing token…` / `401 unknown player key…` · `404 room ZZZZ does not exist … (your current room is VEQR)` ·
`409 the match has not started yet…` (chờ) · `409 room VEQR is over (finished)…` (thoát) · `400 unknown step 'up'` (`do` là động từ,
hướng ở `direction`) · `400 tank is required…` · `400 move does not take foo` · `400 count must be between 1 and 3` · `429` (gọi quá nhiều).

**Nhịp lệnh:** state có `round` và `next_round_in_s`; lệnh gửi đi áp dụng ở nhịp sau (`applies_in_round`). Runner ngủ tới ngay
sau mốc nhịp kế rồi mới lấy state (dữ liệu mới nhất), và coi xe là "đang có lệnh chờ" cho tới `applies_in_round`.

## Cấu hình mặc định
Duel (không NPC) · điều khiển tay · 4 xe/bên · 15 mạng/xe · căn cứ 5 máu, **giáp 3:00 đầu** · tối đa 16 phút thực
(đồng hồ game 8:00, chạy ×0,5 → giáp ≈ 6 phút thực) · nhịp lệnh 0,5s (lệnh hai bên áp dụng cùng lúc ở đầu nhịp sau) ·
mốc 20000 điểm · bản đồ ngẫu nhiên 1/30, 42×42, **đối xứng xoay 180°** · thấy toàn sân. Xe địch đi gần 3 ô/giây.

## Thắng thua
1. Phá hết 5 máu căn cứ địch → thắng ngay. 2. Đạt 20000 điểm trước. 3. Địch mọi xe hết mạng.
4. Hết giờ: nhiều điểm hơn. **Không hoà** — bằng điểm xét: ai đạt điểm đó trước → căn cứ còn nhiều máu →
còn nhiều mạng → xe ở lâu hơn trong nửa sân đối phương → bốc thăm.

Điểm: 1000/máu đại bàng địch · 500/xe hạ · 200/vật phẩm. Bắn trúng lúc đại bàng bất tử, hoặc từ nửa sân nhà: 0 điểm.

## Luật đặc biệt
- **Giáp 3 phút đầu:** tường quanh hai đại bàng là **thép** — chỉ xe **3 sao** bắn vỡ. Lúc này săn xe + nhặt sao.
- **Phải ở nửa sân địch** mới làm căn cứ mất máu: người dưới `y ≤ 19`, người trên `y ≥ 21`. Bắn từ nhà sang chỉ phá tường.
- **Bắn trúng đại bàng → xe bị đưa ngay về điểm xuất phát** (giữ sao, mạng; **mất kế hoạch**). Mỗi máu là một chuyến.
- **Đại bàng bất tử 5 giây sau mỗi phát trúng:** tường quanh nó hoá thép rồi chớp về gạch — nhìn tường là biết lúc bắn được.
  Chủ căn cứ được báo vị trí xe vừa bắn.
- `fire` ≤ 3 viên/lệnh. **Xe không bao giờ tự bắn**, không có lệnh tự giữ nhà.
- Đạn đồng đội không sát thương nhưng **xe đồng đội chặn đạn**. Đạn trúng gì tính nấy, **kể cả căn cứ mình**.
- Xe 2×2 ô, toạ độ `(x, y)` là **ô trên-trái**; x sang phải, y xuống dưới (y=0 hàng trên cùng).
- Bị hạ: hồi sinh sau 3s cạnh căn cứ, khiên 3s, **mất hết sao và mất việc** → agent phải giao lại.

## Địa hình
`.` trống (đi được) · `#` gạch (bắn vỡ) · `@` thép (chỉ 3 sao bắn vỡ) · `~` nước (xe không qua, đạn bay qua) ·
`%` bụi cây (không chặn gì, để nấp) · `-` băng (xe bị trượt).

Bản đồ ASCII còn **vẽ đè các khối 2×2** (nhìn từ phía bạn):
`11` `22` `33` `44` = xe ta (chữ số = số xe) · `OO` = xe địch · `HH` = đại bàng ta (home) · `TT` = đại bàng địch (target) ·
`PP` = vật phẩm. Vật phẩm hay rơi **đè lên gạch**, phải bắn vỡ gạch mới nhặt được: runner nhớ địa hình từng ô qua các nhịp
nên biết dưới `PP` là gạch hay trống; chưa từng thấy thì coi là gạch và đi vào bằng `move_fire` (không có gạch thì nó chỉ đi).
Dưới xe/đại bàng script coi là đất trống, và dùng chúng để lấy vị trí xe/nhà/vật phẩm khi state thiếu trường,
cũng như đoán bên (HH ở nửa dưới → `bottom`).

## Vật phẩm
Mỗi 20s một **cặp** ở hai vị trí đối xứng, tồn tại 15s, chạy đè lên là nhặt.
⭐ sao (1: đạn nhanh · 2: hai viên · 3: bắn thủng thép) · ⛑️ mũ: khiên 10s · ⛏️ xẻng: tường quanh nhà mình thành thép 20s · 🛡️ xe tăng: +1 mạng.

## Bot luyện tập
Dễ: cả đội giữ nhà (nghĩ 4s) · Vừa: nửa công nửa thủ (2s) · Khó: như Vừa + xe công gặp địch là đánh, xe thủ chặn mọi xe vào
nửa sân nhà, nhặt đồ gần đường, nhà còn 2 máu thì rút hết về thủ (0,1s). Thắng đều bot Khó là sẵn sàng đấu người.

## Schema của state (đã xác nhận với server thật)
`GET /matches/{code}/state?map=true` trả về (đã điền sẵn trong `references/state-schema.json`):

| Thông tin | Trường |
|---|---|
| Trạng thái trận | `status`: `scheduled` / `preparing` / `live` / `finished` · kết quả: `result.outcome` (`you_won`/`you_lost`), `result.reason` (vd `forfeit`) |
| Thời gian còn lại / giáp còn lại | `time_left_s` / `bases_open_in_s` (giây đồng hồ game) · `score_limit` · `map_size.width/height` |
| Xe ta | `you.side`, `you.score`, `you.tanks[]`: `tank` (số xe), `x`, `y` (số thực, có thể lẻ như `31.5` vì chuyển động liên tục — làm tròn khi tính ô; ô trên-trái), `facing`, `lives`, `stars`, `alive`, `shielded`, `order` (bước đang làm, vd `{"type":"move","direction":"up","cells":1,"status":"active"}`, hoặc `null`), `next` (các bước chờ, vd `["move(up, 1)","fire"]`). Xe chết: `{"tank","alive":false,"lives","respawns_in_s"}` |
| Xe địch | `opponent.side`, `opponent.score`, `opponent.tanks[]`: như trên nhưng không có `order`/`next` |
| Căn cứ | `bases.yours` / `bases.opponent`: `x`, `y`, `alive`, `hp`, `invulnerable` (5 giây sau khi trúng), `steel_walls_s` (tường thép còn bao lâu: giáp, sau khi trúng, xẻng) |
| Vật phẩm | `powerups[]`: `id`, `type` (`tank`, …), `x`, `y`, `expires_in_s` |
| Nhịp lệnh | `round`, `next_round_in_s` |
| Khác | `npcs[]`, `bullets[]` (`[{x,y,…}]` đạn đang bay — runner né theo đạn thật khi có hướng bay), `note` |
| Bản đồ | `map` (mảng 42 chuỗi) + `map_legend` |
| Sự kiện mới | `events[]`: `seq`, `type`, `time_left_s`, … — đã thấy `order_completed` (`tank`, `order`, `reason`, `steps_left`), `powerup_spawned` (`id`, `powerup`, `x`, `y`), `match_finished` (`outcome`, `reason`, `your_score`, `opponent_score`) |

Ký hiệu bản đồ theo `map_legend`: `1 2` xe bạn (theo số) · `O` xe đối thủ · `N` xe NPC · `H` nhà bạn · `T` nhà đối thủ ·
`x` nhà đã bị phá · `P` vật phẩm · `*` viên đạn.
Chưa thấy mẫu: chi tiết sự kiện `order_failed` (tên trường lý do / ô bị chặn), các trường khác ngoài `x`,`y` của `bullets[]`.

## Toán của trận (cấu hình mặc định)
- Phá nhà: 5 máu × 1000 điểm; 5 phát trúng hợp lệ là **thắng ngay**. Mỗi phát trúng → xe bắn bị đưa về nhà, đại bàng bất tử 5s.
  ⇒ cần nhiều xe đứng sẵn ở các vị trí bắn khác nhau, bắn lần lượt (siege). 4 xe × 1 phát/lượt, mỗi lượt ≥5s.
- Hạ xe: 500 điểm; địch 4 × 15 = 60 mạng → hạ hết là thắng nhưng rất lâu. 20000 điểm = 40 xe.
- Vật phẩm: 200 điểm; ⭐3 bắn thủng thép → mở đường bắn đại bàng xuyên thép.
- Giáp 3:00 game đầu (≈6 phút thực): tường quanh đại bàng là thép → chỉ xe ⭐3 phá được. Xe thường: nhặt sao, chiếm vị trí, săn xe.
- Sau mỗi phát trúng tường quanh đại bàng hoá thép vài giây (bất tử 5s) — trace của script coi thép là chắn nên tự chờ.
- Hết giờ: hơn điểm thắng, không hoà.

## Vai (role) — trong `scripts/bco_brain.py`
| Vai | Hành vi |
|---|---|
| `siege` | Tìm vị trí bắn đại bàng (thẳng hàng, ≤2 lớp gạch, không thép, ở nửa sân địch), mỗi xe một vị trí cách nhau ≥4 ô. Tới nơi thì quay nòng chờ; chỉ bắn khi đại bàng không bất tử **và** đồng đội chưa bắn trong 5,6s. Đang giáp → như `stage`. |
| `rush` | Như siege nhưng không xếp lượt; cũng bắn đại bàng khi tình cờ thẳng hàng. |
| `stage` | Chiếm vị trí bắn đại bàng nhưng không bắn (dùng khi giáp). |
| `hunt` | Tìm ô thẳng hàng với xe địch gần nhất (≤1 lớp gạch, cách ≥2 ô), đi tới rồi bắn. Bỏ qua xe có khiên. |
| `defend` | Chặn xe địch vào gần nhà (bán kính 14) trước; không có thì giữ chốt — các ô quanh nhà có tầm bắn rộng nhất, không nhắm vào nhà ta — quay nòng về phía địch gần nhất. |
| `collect` | Đi nhặt vật phẩm gần nhất theo ưu tiên ⭐ > ⛏️/🛡️ > ⛑️, mỗi món một xe. |
| `siege_left` / `siege_right` / `siege_side` / `siege_front` | Như `siege` nhưng chọn vị trí bắn ở hông trái / hông phải / hông gần nhất / chính diện (theo cột). Không có chỗ phù hợp thì lấy chỗ bất kỳ. |
| `guard_front:N` | Chốt ô cách căn cứ ta N ô về phía địch, nòng hướng về phía địch; địch vào gần thì ra chặn. |
| `<vai>+collect` | Có vật phẩm trong tầm (chi phí đường ≤ 15) thì ghé nhặt, xong làm `<vai>`. |
| `hold` | Đứng yên (vẫn bắn cơ hội). |

## Phản xạ (mọi vai, mỗi nhịp 0,5s, trước việc của vai)
Mọi phát bắn vào xe địch đều **bỏ qua làn mà phía sau mục tiêu là đại bàng ta** (bắn trượt thì đạn bay tiếp vào nhà mình), và
bắn **ít nhất 2 viên** (đạn hai bên gặp nhau thì triệt tiêu, viên sau mới quyết định).
1. **Bắn ngay**: xe địch thẳng làn, không gạch, ≤18 ô (`fire_range`), không có khiên → huỷ việc, bắn.
2. **Bắn đón**: xe địch đang chạy cắt ngang một làn của ta, ≤14 ô → bắn vào chỗ nó sẽ tới
   (vận tốc ước lượng từ các nhịp trước; `bullet_cells_per_s`, `reaction_latency_s` trong state-schema.json).
3. **Né**: bị xe địch nhắm (thẳng làn, không vật chắn, nòng hướng vào ta hoặc ở gần ≤6 ô) mà chưa bắn được
   (vừa bắn, giãn cách 0,6s) → `stop` rồi dạt ngang ra ô trống gần nhất không nằm trong làn địch nào.
   Bị kẹp hai bên không né được → bắn trả ngay.
Đang giáp, vai vây (`siege*`, `rush`) tự chuyển sang `stage`, trừ xe ⭐3.
Tìm đường né các làn bắn hiện tại của xe địch (`danger_penalty` trong schema, mặc định 8, 0 để tắt) và **không phá tường quanh
đại bàng ta** bằng `move_fire` (+80 chi phí; chỉ đi qua khi không còn đường khác).
`hunt` chọn mục tiêu theo **đường đi thực tế** tới chỗ bắn được (xét 3 xe địch gần nhất), không theo khoảng cách chim bay.

## Hàng rào đạn (2 xe địch quay mặt vào nhau bắn liên tục)
Đạn đồng đội không gây sát thương, nên địch có thể đặt 2 xe ở hai đầu một hàng (hoặc cột), quay nòng vào nhau và bắn liên tục.
Xe nào đi vào làn giữa chúng sẽ bị hạ. Runner tự **nhận ra rào** mỗi nhịp: hai xe địch thẳng hàng, cách nhau ≥4 ô, giữa không có
gạch/thép, quay mặt vào nhau. Nếu state không có hướng xe (chỉ có bản đồ) thì nhận theo dấu hiệu cả hai đứng yên thẳng hàng ~2 giây.
Cách giải tự động:
1. **Không đi xuyên rào:** mỗi ô trong làn rào bị cộng +60 chi phí khi tìm đường → xe đi vòng ra ngoài hai đầu rào; chỉ vượt khi
   không còn đường nào khác. Xe đang có **khiên** (vừa hồi sinh, nhặt ⛑️) thì được đi xuyên.
2. **Phá rào từ điểm mù:** xe rào đang quay nòng dọc theo làn của nó → xe ta đến **cùng cột** với nó (rào theo hàng) và bắn;
   nó phải mất ≥1 nhịp quay nòng mới bắn trả được. Mọi lần săn một xe rào đều tự chọn hướng này.
3. `adaptive` tự đổi 1 xe vây/săn thành `breaker` cho mỗi rào (tối đa 2); trong `plan.json` dùng vai `breaker`.
Rào dùng 2/4 xe địch đứng một chỗ → bên công của địch còn 2 xe: giữ 1 xe `defend` là đủ chặn.

## Bị đánh từ hai hông
Địch muốn phá nhà thì phải đứng ở nửa sân ta, thẳng hàng với đại bàng ta, và bắn qua lớp tường (đánh hông = đứng cùng hàng với
đại bàng rồi bắn ngang). Runner xử lý 5 lớp:
1. **Cứu nhà (mọi chiến thuật, ưu tiên cao nhất):** mỗi nhịp tìm xe địch đang có đường đạn tới đại bàng ta (≤2 lớp gạch, đứng ở
   nửa sân ta). Với mỗi xe như vậy, xe ta gần nhất (chi phí đường ≤25, xe đang ở xa thì cứ tiếp tục đánh) sẽ:
   bắn nó ngay nếu thẳng hàng, hoặc tới ô bắn / **ô chắn trong làn đạn**, chọn chỗ rẻ hơn. Xe đứng chắn quay nòng về phía kẻ địch;
   đạn trúng xe thay vì trúng đại bàng (mất 1 mạng xe, còn hơn mất 1 máu nhà).
2. **Mỗi xe thủ một mục tiêu:** `defend`, `guard_left`, `guard_right` không cùng đuổi một xe địch → hai hông có hai người chặn.
3. **Vai `guard_left` / `guard_right`:** chặn xe địch đến từ hông đó; không có địch thì đứng chốt trong làn hông, ngay ngoài tường
   quanh đại bàng, nòng hướng ra ngoài.
4. **`adaptive` tự tách hai hông:** thấy xe địch ở nửa sân ta đến từ **cả trái lẫn phải** → phase `two_flanks`: 1 xe giữ mỗi hông
   (xe gần hông đó nhất), các xe còn lại **vẫn vây đại bàng địch** (địch dồn quân đánh hông thì nhà chúng mỏng — phản công).
   Nhà ta ≤2 máu thì thêm 1 xe `defend`.
5. Mẹo dặn trong prompt: ưu tiên nhặt **xẻng ⛏️** (tường quanh nhà thành thép 20 giây); **đừng tự bắn vỡ** tường hai bên đại bàng.

## Kẹt đường
Lệnh `move` thất bại (`order_failed` / bị từ chối) → runner đọc **ô bị chặn** trong phản hồi (trường `cell`/`blocked`/`at` hoặc
chuỗi "(x, y)"), phạt ô đó +40 trong 8 giây và tính lại đường từ vị trí hiện tại ở nhịp sau → xe tự đi vòng.
Bị từ chối 3 lần liền → **lùi 1-2 ô theo hướng ngược ô bị chặn**, tránh làn bắn của địch, rồi tính lại đường.

## Chiến thuật
- **scripted**: làm đúng chiến thuật người dùng viết — file `plan.json` gồm vai từng xe + luật đổi vai (xem SKILL.md mục 5). Sửa file giữa trận là có hiệu lực ở nhịp sau.
- **adaptive**: trong giáp → `[hunt+collect, collect, hunt+collect, hunt+collect]` (đại bàng đang bọc thép nên kiếm điểm bằng hạ xe
  và vật phẩm; có xe địch ⭐3 thì xe 4 về `defend`), ~25 giây cuối giáp → 2 xe chiếm sẵn vị trí bắn đại bàng (`stage_before_s`);
  bị đánh hai hông → `[siege×2, guard_left, guard_right]` (nhà ≤2 máu: `[siege, guard_left, guard_right, defend]`);
  có hàng rào → thêm `breaker`; xe ⭐3 trong giáp → `siege` (phá được thép); hết giáp → `[siege×3, defend]`;
  đại bàng địch ≤2 máu → tất cả `siege`; nhà ta ≤2 máu hoặc ≥2 xe địch sang sân ta → `[siege×2, defend×2]`;
  cuối trận (≤90s game) dẫn ≥1500 → `[defend×3, hunt]`; cuối trận đang thua → tất cả `siege`.
- **siege** (`defenders=1`), **rush**, **hunter** (`pair=true` để 2 xe một mục tiêu), **fortress** (`hunters=1`, `fire_bricks=1`),
  **collector** (`item_max_cost`, `fallback`), **balanced** (`roles=`).

## Gợi ý điều chỉnh giữa trận (sửa plan.json hoặc control.json)
- Địch dồn quân vây nhà ta → `fortress` hoặc `balanced` với `roles=siege,defend,defend,hunt`.
- Địch thủ chặt nhà, ta mất nhiều xe khi tiếp cận → `hunter` một lúc để kiếm điểm, rồi quay lại `siege`.
- Nhiều ⭐ xuất hiện trong lúc giáp → `collector` (⭐3 bắn thủng thép mở thêm vị trí bắn).
- Bị từ chối lệnh liên tục → xem `rejected` trong log (lý do + ô bị chặn); runner tự phạt ô đó, đi vòng, 3 lần thì lùi ra.
- Gặp hàng rào đạn → runner tự xử lý (mục Hàng rào đạn); muốn dồn lực phá nhanh: `plan.json` cho 2 xe vai `breaker`.

## Thêm chiến thuật mới
Thêm một lớp vào `scripts/bco_brain.py`:
```python
@register
class Ambush(Strategy):
    name = "ambush"
    summary = "2 xe phục giữa sân săn xe, 2 xe vây hông trái."
    def roles(self, game, memory):
        return ["hunt", "hunt", "siege_left", "siege_left"]
```
Xem quyết định của nó: `bco.py analyze bco-data/CODE/state-latest.json --side bottom --strategy ambush`.
