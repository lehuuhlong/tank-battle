---
name: battle-city-online
description: Chơi Battle City Online (tank.recoff.cloud) qua HTTP API tới khi trận kết thúc — chỉ huy 4 xe tăng bằng script Python có tìm đường, bắn thẳng hàng, nhiều chiến thuật (adaptive, siege, rush, hunter, fortress, collector, balanced, scripted theo prompt), đổi chiến thuật giữa trận. Dùng khi người dùng nói "chơi tank", "battle city", "BCO", "xe tăng", đưa mã phòng 4 ký tự kèm chiến thuật, hoặc muốn phân tích chiến thuật cho game này.
---

# Battle City Online — chỉ huy đội xe tăng

Bạn chỉ huy 4 xe tăng. Mục tiêu: **thắng trận**, bằng một trong bốn cách: phá 5 máu đại bàng địch, đạt 20000 điểm trước, làm địch hết mạng, hoặc hơn điểm khi hết giờ.
Việc điều khiển từng nhịp do `bco.py run` lo: chạy nền, mỗi 0,5 giây làm một lượt gồm lấy state + bản đồ, tính quyết định (khoảng 40 ms),
rồi gửi một `/plan`. Nhịp tự giãn ra khi gặp `429`. Mỗi nhịp, các xe phản xạ trước khi làm việc của vai: bắn ngay khi địch thẳng hàng,
bắn đón khi địch sắp cắt ngang làn, né khi bị nhắm mà chưa bắn trả được. Việc của bạn:
- chuẩn bị trước trận;
- dịch chiến thuật của người dùng thành kế hoạch;
- **giám sát tới hết trận** và chỉnh khi cần.

Luật, API và chi tiết các vai/chiến thuật: `references/rules.md`.

Lệnh dùng trong file này: `SK=~/.claude/skills/battle-city-online/scripts` (hoặc `.claude/skills/battle-city-online/scripts` trong project).
Gọi bằng `python "$SK/bco.py" <lệnh>`; trên Linux/macOS dùng `python3`. Chỉ cần Python 3.9+, không phải cài thêm gì.
Dữ liệu từng trận nằm trong `./bco-data/<CODE>/`: match.json, rules.json, state-latest.json, plan.json, control.json, log.jsonl.

## 1. Hợp đồng vòng lặp
- **Chơi tới khi trận kết thúc**, tức là khi `bco.py watch` in `TRẬN KẾT THÚC` (mã thoát 3). Không dừng giữa chừng, **không hỏi lại người dùng giữa trận**.
- Mỗi vòng giám sát chỉ nghĩ 1-2 câu và in **một dòng**: thời gian · điểm · máu nhà · chiến thuật · việc đáng chú ý.
- Khi người dùng bấm Esc và gõ chỉ đạo mới: sửa kế hoạch (mục 6), rồi **tiếp tục giám sát** tới hết trận. Trong lúc đó runner vẫn chạy nền nên xe vẫn đánh.
- Chỉ dừng sớm khi: thiếu token hoặc gặp 401 lặp lại, phòng không tồn tại, hoặc người dùng bảo dừng.

## 2. Kết nối
- Token nằm trong `BCO_TOKEN` (gửi qua header `Authorization: Bearer`). **Không bao giờ in token** hay ghi nó ra file.
  Thiếu token thì báo người dùng đặt `$env:BCO_TOKEN="..."` (PowerShell) hoặc `export BCO_TOKEN=...`, rồi mở lại Claude Code.
- Đặt `BCO_AGENT_MODEL` bằng tên model bạn đang chạy; giá trị này được gửi trong header `X-Agent-Model`.
- API mặc định là `http://tank.recoff.cloud/api/v1` (đổi bằng `BCO_BASE_URL`).
- Mã phòng là 4 ký tự trong prompt. Không có mã thì chạy `bco.py matches --playing`.

## 3. Chuẩn bị trước trận
1. `bco.py info CODE` — **gọi ngay**, vì nút Sẵn sàng của người dùng chỉ bật khi agent đã gọi API cho phòng.
   Lệnh này in ra bạn ở bên `top` hay `bottom`. Không đoán được thì đọc `bco-data/CODE/match.json` và dùng `--side` ở các bước sau.
2. `bco.py rules CODE` — chỉ đọc một lần. Nếu **luật trận khác skill thì tin luật trận**. Số liệu khác mặc định (thời gian, giáp, ranh giới nửa sân) thì sửa trong `references/state-schema.json`.
3. `state-schema.json` **đã điền theo JSON thật** của server. Chỉ khi `bco.py state CODE --summary` in cảnh báo (`!`)
   hoặc server đổi định dạng mới cần dò lại:
   - chạy `bco.py state CODE`, rồi `bco.py probe bco-data/CODE/state-latest.json --side <bên>`;
   - trường nào chưa tìm thấy thì đọc mục Tài liệu API trên trang Hướng dẫn của game và điền vào `state-schema.json` (`--write` tự ghi các trường đã dò được);
   - bản đồ có ký tự lạ thì thêm vào `legend`;
   - ghi lại bảng schema ở `references/rules.md` để lần sau không phải dò nữa.
4. Dịch chiến thuật của người dùng thành `bco-data/CODE/plan.json` (mục 5). Người dùng không nêu chiến thuật thì dùng `adaptive`.
5. Kiểm tra: `bco.py analyze bco-data/CODE/state-latest.json --side <bên> --strategy scripted --param plan=bco-data/CODE/plan.json`.
6. Khởi động runner **tách nền**: `bco.py run CODE --strategy scripted --detach` (hoặc `--strategy adaptive`).
   Lệnh trả về ngay; runner chạy thành tiến trình riêng, ghi log ở `bco-data/CODE/runner.log`.
   **Không bao giờ chạy `bco.py run` không có `--detach`**: lệnh Bash thường của Claude Code bị dừng sau ~2 phút, runner chết
   theo và xe đứng yên → thua. **Không tự ra lệnh từng xe bằng tay thay cho runner** (mỗi lần mất vài giây, bot phản ứng mỗi 0,1 giây).
   Nếu trận chưa `live`, runner tự chờ; nhắc người dùng bấm **Sẵn sàng**.

## 4. Bản đồ và toạ độ
- Lưới 42×42; `x` tăng sang phải, `y` tăng xuống dưới, `y=0` là hàng trên cùng. Xe và đại bàng đều là khối 2×2, toạ độ là ô trên-trái.
- Ký hiệu:
  - `.` trống;
  - `#` gạch (đi qua bằng `move_fire`);
  - `@` thép (tránh; xe 3 sao bắn vỡ được);
  - `~` nước (xe không qua, đạn bay qua);
  - `%` bụi cây (đi được, dùng để nấp);
  - `-` băng.
- Bản đồ vẽ đè các khối 2×2: `11`-`44` là xe ta (chữ số là số xe), `OO` xe địch, `HH` nhà ta, `TT` nhà địch, `PP` vật phẩm.
  Script tự đọc các ký hiệu này khi state thiếu trường, và đoán bên của ta từ vị trí `HH`.
  Vật phẩm hay rơi đè lên gạch. Runner nhớ địa hình dưới `PP` từ các nhịp trước, chưa biết thì coi là gạch, và tự đi vào bằng `move_fire` để nhặt.
- Thẳng hàng để bắn: cùng cột khi `|Δx| ≤ 1`, cùng hàng khi `|Δy| ≤ 1`. Giữa hai bên không được có thép, xe ta, hay **đại bàng ta**.
- Muốn phá nhà phải đứng ở nửa sân địch (bên dưới: `y ≤ 19`; bên trên: `y ≥ 21`). Đánh vào hông thường dễ hơn đánh chính diện.

## 5. Dịch chiến thuật của người dùng → plan.json
```json
{"roles": {"1": "siege_left", "2": "siege_left", "3": "hunt+collect", "4": "guard_front:3"},
 "rules": [
   {"when": {"armor": true}, "roles": {"1": "hunt+collect", "2": "hunt+collect", "3": "hunt+collect"}},
   {"when": {"my_base_hp_lte": 2}, "roles": {"3": "defend"}},
   {"when": {"my_base_hit": true}, "roles": {"4": "defend"}},
   {"when": {"time_left_lte": 60, "lead_gte": 1}, "roles": {"*": "defend"}}]}
```
Vai dùng được:
- `siege`: vây đại bàng, các xe bắn lần lượt cách nhau hơn 5 giây.
- `siege_left`, `siege_right`, `siege_side`, `siege_front`: như `siege` nhưng chọn hông để đánh.
- `rush`: lao vào bắn, không xếp lượt.
- `stage`: chiếm sẵn vị trí bắn nhưng chưa bắn.
- `hunt`: săn xe địch.
- `breaker`: phá hàng rào đạn (2 xe địch quay mặt vào nhau bắn chặn một hàng/cột): đánh xe rào từ phía nó không quay nòng. Không có rào thì như `hunt`.
- `defend`: chặn địch gần nhà hoặc giữ chốt.
- `guard_front:N`: chốt trước nhà N ô.
- `guard_left`, `guard_right`: giữ hông trái / phải của đại bàng ta (chặn xe địch đến từ phía đó, không có thì chốt trong làn hông).
- `collect`: nhặt vật phẩm.
- `hold`: đứng yên.
- Hậu tố `+collect`: ghé nhặt đồ gần đường rồi làm vai chính.

Ngoài vai, runner luôn ưu tiên **cứu nhà**: xe địch nào có đường đạn tới đại bàng ta thì xe ta gần nhất bắn nó hoặc lao vào chắn làn đạn.
Mọi vai đều tự bắn xe địch khi thẳng hàng. Khi còn giáp, các vai vây tự chuyển sang `stage`, trừ xe ⭐3.

Điều kiện trong `when`:
- chung: `armor`, `my_base_hp_lte/gte`, `enemy_base_hp_lte/gte`, `time_left_lte/gte` (**giây đồng hồ game**), `lead_gte/lte`, `invaders_gte`, `my_base_hit`, `enemy_alive_lte`;
- theo từng xe: `tank_lives_lte/gte`, `tank_stars_gte`.

Các luật được xét theo thứ tự, luật sau ghi đè luật trước. Ý nào của người dùng không diễn đạt được bằng vai/điều kiện thì chọn vai gần nhất và nói rõ trong một dòng.

## 6. Vòng giám sát
Lặp: `bco.py watch CODE --seconds 25 --ensure` → in 1 dòng → xử lý nếu cần → lặp lại.
`--ensure`: nếu runner đã chết mà trận chưa xong, watch **tự khởi động lại** runner với tham số cũ (và báo lại).
- Mã thoát 3 hoặc thấy `TRẬN KẾT THÚC`: sang mục 8.
- Mã thoát 4 (runner đã chết, không có `--ensure` hoặc chưa từng `--detach`): chạy `bco.py run CODE … --detach`, xem lý do trong `runner.log`.
- Đổi chiến thuật **mà không dừng runner**:
  - sửa `bco-data/CODE/plan.json` (runner đọc lại ngay ở nhịp sau), hoặc
  - ghi `bco-data/CODE/control.json`, ví dụ `{"strategy":"fortress","params":{"hunters":"1"}}`, `{"pause":true}` hay `{"quit":true}`.
- Lệnh tay gấp: `bco.py plan CODE 2:stop 2:move:down:4 2:fire:up:3` (đặt `stop` đầu tiên để thay việc). Runner có thể ghi đè lệnh này ở nhịp sau, nên muốn giữ lâu thì sửa vai trong plan.json.

## 7. Xử lý sự kiện (runner tự làm; bạn theo dõi)
| Sự kiện | Runner tự làm | Bạn làm |
|---|---|---|
| Lệnh bị từ chối / `order_failed` (ô bị chặn nằm trong `reason`) | Đọc ô bị chặn từ `reason`, phạt ô đó 8 giây và tính đường vòng; 3 lần liền thì lùi 1-2 ô ngược hướng ô bị chặn | Nếu lặp lại nhiều: đọc lý do trong `log.jsonl`, sửa schema hoặc legend |
| Địch dựng hàng rào đạn (2 xe quay mặt vào nhau bắn chặn một hàng) | Không đi xuyên làn rào (trừ khi có khiên); `adaptive` cử xe `breaker` đánh xe rào từ phía nó không quay nòng | Muốn phá nhanh: cho 2 xe vai `breaker` trong plan.json |
| Xe bị hạ rồi hồi sinh, hoặc xe bị đưa về nhà sau khi bắn trúng đại bàng | Giao lại việc | — |
| Xe địch có đường đạn tới đại bàng ta | Xe ta gần nhất bắn nó hoặc lao vào chắn làn (mọi chiến thuật) | — |
| Địch đánh từ hai hông | Mỗi xe thủ nhận một kẻ địch khác nhau; `adaptive` đặt `guard_left` + `guard_right`, các xe còn lại vẫn công | Dùng `scripted`: cho 2 xe `guard_left`/`guard_right`; dặn ưu tiên nhặt xẻng ⛏️ |
| Nhà ta bị bắn | Vai `defend` mở rộng vùng chặn trong 8 giây | `watch` báo "NHÀ TA MẤT MÁU": cân nhắc thêm vai `defend` hoặc `guard_front` |
| `429` (gọi API quá nhiều) | Client tự chờ rồi gửi lại | Vẫn bị: tăng `--interval` |
| `404 room … (your current room is XXXX)` | Runner dừng và in thông báo | Chạy lại `bco.py run` với mã phòng hiện tại `XXXX` |
| `409 the match has not started yet` / trạng thái `scheduled`, `preparing` | Runner tự chờ, không ra lệnh | Nhắc người dùng bấm Sẵn sàng |
| `409 room … is over (finished)` | Runner thấy `finished` và dừng | Sang mục 8 |

## 8. Khi trận kết thúc
Báo cho người dùng: thắng hay thua, điểm, máu nhà hai bên, các chiến thuật đã dùng và đã đổi, lỗi hoặc chỗ xe kẹt đáng chú ý.
Đề xuất 1-3 điều chỉnh cho trận Đấu lại. Nếu người dùng muốn đấu tiếp thì làm lại từ mục 3 (bước 1, 4, 6).
