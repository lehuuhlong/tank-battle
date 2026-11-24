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
3. **Lần đầu dùng với server thật**, khi `state-schema.json` còn toàn `null`:
   - chạy `bco.py state CODE`, rồi `bco.py probe bco-data/CODE/state-latest.json --side <bên>`;
   - trường nào chưa tìm thấy thì đọc mục Tài liệu API trên trang Hướng dẫn của game và điền vào `state-schema.json` (`--write` tự ghi các trường đã dò được);
   - bản đồ có ký tự lạ thì thêm vào `legend`;
   - ghi lại bảng schema ở `references/rules.md` để lần sau không phải dò nữa.
4. Dịch chiến thuật của người dùng thành `bco-data/CODE/plan.json` (mục 5). Người dùng không nêu chiến thuật thì dùng `adaptive`.
5. Kiểm tra: `bco.py analyze bco-data/CODE/state-latest.json --side <bên> --strategy scripted --param plan=bco-data/CODE/plan.json`.
6. Chạy nền (Bash `run_in_background`): `bco.py run CODE --strategy scripted` (hoặc `--strategy adaptive`).
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
- `defend`: chặn địch gần nhà hoặc giữ chốt.
- `guard_front:N`: chốt trước nhà N ô.
- `collect`: nhặt vật phẩm.
- `hold`: đứng yên.
- Hậu tố `+collect`: ghé nhặt đồ gần đường rồi làm vai chính.

Mọi vai đều tự bắn xe địch khi thẳng hàng. Khi còn giáp, các vai vây tự chuyển sang `stage`, trừ xe ⭐3.

Điều kiện trong `when`:
- chung: `armor`, `my_base_hp_lte/gte`, `enemy_base_hp_lte/gte`, `time_left_lte/gte` (**giây đồng hồ game**), `lead_gte/lte`, `invaders_gte`, `my_base_hit`, `enemy_alive_lte`;
- theo từng xe: `tank_lives_lte/gte`, `tank_stars_gte`.

Các luật được xét theo thứ tự, luật sau ghi đè luật trước. Ý nào của người dùng không diễn đạt được bằng vai/điều kiện thì chọn vai gần nhất và nói rõ trong một dòng.

## 6. Vòng giám sát
Lặp: `bco.py watch CODE --seconds 25` → in 1 dòng → xử lý nếu cần → lặp lại.
- Mã thoát 3 hoặc thấy `TRẬN KẾT THÚC`: sang mục 8.
- Mã thoát 4 (runner đã chết): xem output của lệnh nền rồi chạy lại `bco.py run`.
- Đổi chiến thuật **mà không dừng runner**:
  - sửa `bco-data/CODE/plan.json` (runner đọc lại ngay ở nhịp sau), hoặc
  - ghi `bco-data/CODE/control.json`, ví dụ `{"strategy":"fortress","params":{"hunters":"1"}}`, `{"pause":true}` hay `{"quit":true}`.
- Lệnh tay gấp: `bco.py plan CODE 2:stop 2:move:down:4 2:fire:up:3` (đặt `stop` đầu tiên để thay việc). Runner có thể ghi đè lệnh này ở nhịp sau, nên muốn giữ lâu thì sửa vai trong plan.json.

## 7. Xử lý sự kiện (runner tự làm; bạn theo dõi)
| Sự kiện | Runner tự làm | Bạn làm |
|---|---|---|
| Lệnh bị từ chối / `order_failed` | Gửi lại riêng xe đó; sau 3 lần liền thì cho xe nhích 1 ô để thoát kẹt | Nếu lặp lại nhiều: đọc lý do trong `log.jsonl`, sửa schema hoặc legend |
| Xe bị hạ rồi hồi sinh, hoặc xe bị đưa về nhà sau khi bắn trúng đại bàng | Giao lại việc | — |
| Nhà ta bị bắn | Vai `defend` mở rộng vùng chặn trong 8 giây | `watch` báo "NHÀ TA MẤT MÁU": cân nhắc thêm vai `defend` hoặc `guard_front` |
| `429` (gọi API quá nhiều) | Client tự chờ rồi gửi lại | Vẫn bị: tăng `--interval` |

## 8. Khi trận kết thúc
Báo cho người dùng: thắng hay thua, điểm, máu nhà hai bên, các chiến thuật đã dùng và đã đổi, lỗi hoặc chỗ xe kẹt đáng chú ý.
Đề xuất 1-3 điều chỉnh cho trận Đấu lại. Nếu người dùng muốn đấu tiếp thì làm lại từ mục 3 (bước 1, 4, 6).
