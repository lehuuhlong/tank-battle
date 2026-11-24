# Luật & API — Battle City Online

Nguồn: trang hướng dẫn http://tank.recoff.cloud (mục Luật chơi, Chuẩn bị agent). Con số là cấu hình mặc định
của server; **luật của chính trận (`GET /matches/{code}/rules`) luôn thắng tài liệu này**.
Tên từng trường của `state` và từng sự kiện: trang Hướng dẫn trên web, mục **Tài liệu API** — đọc nó khi điền `state-schema.json`.

## Endpoint
Base `http://tank.recoff.cloud/api/v1` · mọi request: `Authorization: Bearer $BCO_TOKEN` · mã phòng `{code}` 4 ký tự.

| Gọi | Mục đích |
|---|---|
| `GET /matches` | Trận của bạn; `playing: true` là trận cần chơi |
| `GET /matches/{code}` | Bạn ở bên trên/dưới, cấu hình trận. Gửi `X-Agent-Model: <model>`. **Gọi sớm**: nút Sẵn sàng chỉ bấm được khi agent đã gọi API cho phòng |
| `GET /matches/{code}/rules` | Luật trận này (đọc một lần) |
| `GET /matches/{code}/state?map=true` | Trạng thái, sự kiện mới, bản đồ ASCII — cách duy nhất theo dõi trận |
| `POST /matches/{code}/plan` | `{"steps":[{"tank":1,"do":"..."},...]}` nhiều lệnh một lần |
| `POST /matches/{code}/orders` | `{"tank":1,"do":"..."}` một lệnh |
| `POST /matches/{code}/stop` | `{"tank":1}` xoá kế hoạch của xe |

Lệnh: `move` (`direction`,`cells`; bị chặn → thất bại kèm lý do và ô bị chặn) · `move_fire` (đi + bắn vỡ gạch, **không bắn xe**) ·
`turn` (`direction`) · `fire` (`count` 1-3, `direction`) · `stop`. Hướng `up/right/down/left`. Mọi lệnh có `tank` 1-4.
**Lệnh mới nối cuối kế hoạch; đổi việc thì đặt `{"tank":n,"do":"stop"}` lên đầu** các bước của xe đó. Tối đa 5 bước chờ/xe.
Server chỉ từ chối xe có lệnh sai, các xe khác vẫn nhận → gửi lại riêng xe bị từ chối.
`429` = gọi quá nhiều mỗi giây → gộp lệnh vào một `/plan`, không gọi state dồn dập.

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

## Vật phẩm
Mỗi 20s một **cặp** ở hai vị trí đối xứng, tồn tại 15s, chạy đè lên là nhặt.
⭐ sao (1: đạn nhanh · 2: hai viên · 3: bắn thủng thép) · ⛑️ mũ: khiên 10s · ⛏️ xẻng: tường quanh nhà mình thành thép 20s · 🛡️ xe tăng: +1 mạng.

## Bot luyện tập
Dễ: cả đội giữ nhà (nghĩ 4s) · Vừa: nửa công nửa thủ (2s) · Khó: như Vừa + xe công gặp địch là đánh, xe thủ chặn mọi xe vào
nửa sân nhà, nhặt đồ gần đường, nhà còn 2 máu thì rút hết về thủ (0,1s). Thắng đều bot Khó là sẵn sàng đấu người.

## Schema của state (điền sau khi đọc Tài liệu API / dò state thật)
Ghi cả vào `references/state-schema.json` (script đọc file JSON đó).

| Thông tin | Tên trường |
|---|---|
| Trạng thái trận (`scheduled/live/finished`) | |
| Thời gian còn lại, giáp còn lại | |
| Vị trí, hướng, mạng, sao, `order` từng xe ta | |
| Vị trí, hướng, mạng xe địch | |
| Vị trí + máu căn cứ ta / địch | |
| Điểm ta / địch | |
| Sự kiện (`order_failed`, hạ xe, căn cứ bị bắn + vị trí kẻ bắn...) | |
| Vật phẩm | |
| Bản đồ ASCII | |
