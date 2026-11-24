# Chiến thuật — chi tiết

## Toán của trận (cấu hình mặc định)
- Phá nhà: 5 máu × 1000 điểm; 5 phát trúng hợp lệ là **thắng ngay**. Mỗi phát trúng → xe bắn bị đưa về nhà, đại bàng bất tử 5s.
  ⇒ cần nhiều xe đứng sẵn ở các vị trí bắn khác nhau, bắn lần lượt (siege). 4 xe × 1 phát/lượt, mỗi lượt ≥5s.
- Hạ xe: 500 điểm; địch 4 × 15 = 60 mạng → hạ hết là thắng nhưng rất lâu. 20000 điểm = 40 xe.
- Vật phẩm: 200 điểm; ⭐3 bắn thủng thép → mở đường bắn đại bàng xuyên thép.
- Giáp 3:00 game đầu (≈6 phút thực): tường quanh đại bàng là thép → chỉ xe ⭐3 phá được. Xe thường: nhặt sao, chiếm vị trí, săn xe.
- Sau mỗi phát trúng tường quanh đại bàng hoá thép vài giây (bất tử 5s) — trace của script coi thép là chắn nên tự chờ.
- Hết giờ: hơn điểm thắng, không hoà.

## Vai (role) — khối dựng trong `bco_lib/tactics.py`
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

Mọi vai (trừ `stage`) trước tiên kiểm tra **bắn cơ hội**: xe địch đang nằm thẳng làn, không gạch, ≤18 ô (`fire_range`) → bắn ngay.
Đang giáp, vai vây (`siege*`, `rush`) tự chuyển sang `stage`, trừ xe ⭐3.
Tìm đường né các làn bắn hiện tại của xe địch (`danger_penalty` trong schema, 0 để tắt).

## Chiến thuật
- **scripted**: làm đúng chiến thuật người dùng viết — file `plan.json` gồm vai từng xe + luật đổi vai (xem SKILL.md mục 5,
  ví dụ `plan-example.json`, dịch từ prompt mẫu của trang hướng dẫn). Sửa file giữa trận là có hiệu lực ở nhịp sau.
- **adaptive**: giáp → `[collect, stage, hunt, defend]` (xe ⭐3 chuyển sang `siege`); hết giáp → `[siege×3, defend]`;
  đại bàng địch ≤2 máu → tất cả `siege`; nhà ta ≤2 máu hoặc ≥2 xe địch sang sân ta → `[siege×2, defend×2]`;
  cuối trận (≤90s game) dẫn ≥1500 → `[defend×3, hunt]`; cuối trận đang thua → tất cả `siege`.
- **siege** (`defenders=1`), **rush**, **hunter** (`pair=true` để 2 xe một mục tiêu), **fortress** (`hunters=1`, `fire_bricks=1`),
  **collector** (`item_max_cost`, `fallback`), **balanced** (`roles=`).

## Gợi ý điều chỉnh khi đang chơi (ghi control.json)
- Địch dồn quân vây nhà ta → `fortress` hoặc `balanced` với `roles=siege,defend,defend,hunt`.
- Địch thủ chặt nhà, ta mất nhiều xe khi tiếp cận → `hunter` một lúc để kiếm điểm, rồi quay lại `siege`.
- Nhiều ⭐ xuất hiện trong lúc giáp → `collector` (⭐3 bắn thủng thép mở thêm vị trí bắn).
- Bị từ chối lệnh liên tục → xem `rejected` trong log; runner tự lùi về lệnh đầu, sau 3 lần thì `unstick`.

## Thêm chiến thuật mới
```python
# scripts/strategies/ambush.py
from strategies import register
from strategies.base import Strategy

@register
class Ambush(Strategy):
    name = "ambush"
    summary = "2 xe phục ở giữa sân, 2 xe vây."
    def roles(self, game, memory):
        return ["hunt", "hunt", "siege", "siege"]
```
Thử ngay: `analyze_state.py fixtures/sample_state.json --side bottom --strategy ambush`, rồi `bench.py ambush`.
