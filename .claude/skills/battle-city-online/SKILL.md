---
name: battle-city-online
description: Chơi Battle City Online (tank.recoff.cloud) qua HTTP API tới khi trận kết thúc — chỉ huy 4 xe tăng bằng script Python có tìm đường, bắn thẳng hàng, nhiều chiến thuật (adaptive, siege, rush, hunter, fortress, collector, balanced, scripted theo prompt), đổi chiến thuật giữa trận. Dùng khi người dùng nói "chơi tank", "battle city", "BCO", "xe tăng", đưa mã phòng 4 ký tự kèm chiến thuật, hoặc muốn phân tích/thử chiến thuật cho game này.
---

# Battle City Online — chỉ huy đội xe tăng

Bạn là chỉ huy 4 xe tăng. Mục tiêu: **thắng trận** (phá 5 máu đại bàng địch / 20000 điểm trước / địch hết mạng / hơn điểm khi hết giờ).
Việc điều khiển từng nhịp do script `run_strategy.py` làm (nhanh, không tốn suy nghĩ). Việc của bạn: chuẩn bị, dịch chiến thuật
của người dùng thành kế hoạch, **giám sát tới hết trận**, chỉnh khi cần.

Luật đầy đủ + API: `references/quick-reference.md`. Vai & chiến thuật: `references/strategies.md`. Ví dụ kế hoạch: `references/plan-example.json`.

## 1. Hợp đồng vòng lặp
- **Chơi tới khi trận kết thúc** (`watch.py` in `TRẬN KẾT THÚC`, mã thoát 3). Không dừng giữa chừng, **không hỏi lại người dùng giữa trận**.
- Mỗi vòng giám sát: nghĩ 1-2 câu, in **một dòng** trạng thái (thời gian · điểm · máu nhà · chiến thuật · việc đáng chú ý).
- Người dùng bấm Esc và gõ chỉ đạo mới → sửa kế hoạch (mục 6), rồi **tiếp tục vòng giám sát** tới hết trận.
  Trong lúc bạn bị ngắt, `run_strategy.py` vẫn chạy nền nên xe vẫn đánh.
- Chỉ dừng sớm khi: không có token / 401 lặp lại, phòng không tồn tại, hoặc người dùng bảo dừng (`{"quit":true}` vào control.json).

## 2. Kết nối
- Token: biến môi trường `BCO_TOKEN` (header `Authorization: Bearer`). **Không bao giờ in token**, không ghi vào file.
  Thiếu token → báo người dùng đặt (`$env:BCO_TOKEN = "..."` PowerShell / `export BCO_TOKEN=...` bash) rồi mở lại Claude Code.
- Đặt `BCO_AGENT_MODEL` = tên model bạn đang chạy (gửi trong header `X-Agent-Model`).
- API mặc định `http://tank.recoff.cloud/api/v1` (đổi bằng `BCO_BASE_URL`).
- Mã phòng: 4 ký tự trong prompt. Không có → `list_matches.py --playing`.
- Script: `SK=~/.claude/skills/battle-city-online/scripts` (hoặc `.claude/skills/...` trong project). Chạy bằng
  `python "$SK/<script>.py"` — trên Windows dùng `python` (hoặc `py`), Linux/macOS `python3`. Chỉ cần Python 3.9+, không cài thêm gì.
- Dữ liệu trận: `./bco-data/<CODE>/` (match.json, rules.json, state-latest.json, plan.json, control.json, log.jsonl).

## 3. Chuẩn bị trước trận (theo thứ tự)
1. `python "$SK/match_info.py" CODE` — **gọi ngay**: nút Sẵn sàng của người dùng chỉ bật khi agent đã gọi API cho phòng.
   In ra bên `top/bottom`. Không đoán được → đọc `bco-data/CODE/match.json`, dùng `--side` ở các bước sau.
2. `python "$SK/get_rules.py" CODE` — đọc một lần. **Luật trận khác skill → tin luật trận**; số khác mặc định (thời gian, giáp,
   nửa sân) thì sửa `references/state-schema.json` (`game_seconds_total`, `armor_game_seconds`, `enemy_half`).
3. **Lần đầu dùng với server thật** (schema còn toàn `null`): `get_state.py CODE` rồi
   `probe_schema.py bco-data/CODE/state-latest.json --side <bên>`. Trường nào chưa tìm thấy → đọc mục Tài liệu API trên
   trang Hướng dẫn của game và điền đường dẫn vào `state-schema.json` (`--write` ghi những gì đã tự dò được). Kiểm tra
   `extract_map.py` không báo ký tự lạ. Ghi lại bảng schema ở cuối `references/quick-reference.md` để lần sau khỏi dò.
4. Dịch chiến thuật của người dùng thành `bco-data/CODE/plan.json` (mục 5). Không có chiến thuật → dùng `adaptive`.
5. Kiểm tra nhanh: `analyze_state.py bco-data/CODE/state-latest.json --side <bên> --strategy scripted --param plan=bco-data/CODE/plan.json`
   (in vai/lệnh của từng xe). Vai lạ sẽ bị cảnh báo.
6. Chạy nền (Bash `run_in_background`):
   `python "$SK/run_strategy.py" CODE --strategy scripted` (hoặc `--strategy adaptive`).
   Trận chưa `live` thì script tự chờ ("chờ trận bắt đầu") — nhắc người dùng bấm **Sẵn sàng**.

## 4. Bản đồ và toạ độ (script đã xử lý — cần hiểu khi đọc log / ra lệnh tay)
- Lưới 42×42, `x` sang phải, `y` xuống dưới, `y=0` hàng trên. Xe 2×2, toạ độ = ô trên-trái. Đại bàng cũng 2×2.
- `.` trống · `#` gạch (đi qua bằng `move_fire`) · `@` thép (tránh; 3 sao bắn vỡ) · `~` nước (tránh, đạn qua) · `%` bụi (đi được, nấp) · `-` băng.
- Thẳng hàng để bắn: cùng cột khi `|x_ta − x_địch| ≤ 1`, cùng hàng khi `|y_ta − y_địch| ≤ 1`; giữa hai bên không có thép,
  không có xe ta, **không có đại bàng ta**. Mỗi lớp gạch cần thêm 1 viên.
- Phá nhà: đứng trong nửa sân địch (dưới: `y ≤ 19`, trên: `y ≥ 21`), thẳng hàng với đại bàng địch. Hông trái/phải thường dễ hơn chính diện.

## 5. Dịch chiến thuật của người dùng → plan.json (chiến thuật `scripted`)
```json
{"roles": {"1": "siege_left", "2": "siege_left", "3": "hunt+collect", "4": "guard_front:3"},
 "rules": [
   {"when": {"armor": true}, "roles": {"1": "hunt+collect", "2": "hunt+collect", "3": "hunt+collect"}},
   {"when": {"my_base_hp_lte": 2}, "roles": {"3": "defend"}},
   {"when": {"time_left_lte": 60, "lead_gte": 1}, "roles": {"*": "defend"}}]}
```
Vai: `siege` (vây đại bàng, bắn lần lượt cách >5s) · `siege_left`/`siege_right`/`siege_side`/`siege_front` (chọn hông) · `rush`
(bắn không xếp lượt) · `stage` (chiếm chỗ, chưa bắn) · `hunt` · `defend` (chặn địch gần nhà / giữ chốt) · `guard_front:N`
(chốt trước nhà N ô, nòng về phía địch) · `collect` · `hold` · hậu tố `+collect` (ghé nhặt đồ gần đường).
Mọi vai đều tự bắn xe địch thẳng hàng; vai vây tự chuyển `stage` khi còn giáp (trừ xe 3 sao).
Điều kiện: `armor`, `my_base_hp_lte/gte`, `enemy_base_hp_lte/gte`, `time_left_lte/gte` (**giây đồng hồ game**), `lead_gte/lte`,
`invaders_gte`, `my_base_hit`, `enemy_alive_lte`, theo từng xe `tank_lives_lte/gte`, `tank_stars_gte`. Luật sau ghi đè luật trước.
"Đi cặp", "men mép trái": dùng cùng một vai cho hai xe + `siege_left`. Ý nào không diễn đạt được → nói rõ với người dùng
một dòng, chọn vai gần nhất.

## 6. Vòng giám sát (sau khi runner chạy)
Lặp: `python "$SK/watch.py" CODE --seconds 25` → in 1 dòng → xử lý → lặp.
- Mã 3 / `TRẬN KẾT THÚC` → sang mục 8. Mã 4 (runner chết) → xem output nền, chạy lại `run_strategy.py`.
- Đổi chiến thuật **không dừng runner**: sửa `bco-data/CODE/plan.json` (scripted đọc lại ngay), hoặc ghi
  `bco-data/CODE/control.json`: `{"strategy":"fortress","params":{"hunters":"1"}}` · `{"pause":true}` · `{"pause":false}` · `{"quit":true}`.
- Lệnh tay gấp: `send_plan.py CODE 2:stop 2:move:down:4 2:fire:up:3` (stop đứng đầu để thay việc). Runner có thể ghi đè ở nhịp sau
  → muốn giữ lâu thì đổi vai trong plan.json, hoặc `pause` trước.

## 7. Xử lý sự kiện (runner tự làm phần lớn — biết để giám sát)
| Sự kiện | Phản ứng tự động | Bạn làm gì |
|---|---|---|
| `order_failed` / lệnh bị từ chối | gửi lại riêng xe đó (stop + bước đầu); 3 lần liền → nhích 1 ô thoát kẹt | lặp lại nhiều → xem lý do trong `log.jsonl`, sửa schema/legend |
| Xe bị hạ, hồi sinh (mất việc, mất sao) | phát hiện xe nhảy về nhà → giao lại việc | — |
| Bắn trúng đại bàng → xe về nhà | giao lại việc; đồng đội chờ hết 5s bất tử mới bắn | — |
| Căn cứ ta bị bắn | vai `defend` mở rộng vùng chặn 8s | `watch` báo "NHÀ TA MẤT MÁU" → cân nhắc thêm `defend`/`guard_front` |
| Vật phẩm xuất hiện | vai `collect`/`+collect` đi nhặt | đang giáp → ưu tiên ⭐ |
| `429` | client tự lùi lại | nếu vẫn bị → tăng `--interval` |
| Cảnh báo schema/bản đồ | — | sửa `state-schema.json` rồi chạy lại runner |

## 8. Khi trận kết thúc
Báo: thắng/thua, điểm, máu nhà hai bên, chiến thuật đã dùng và các lần đổi, lỗi/kẹt đáng chú ý (đếm lệnh bị từ chối trong log).
Đề xuất 1-3 điều chỉnh cho trận Đấu lại (vd: "địch dồn công → thêm guard_front", "xe kẹt ở góc thép trái → siege_right").
Người dùng muốn đấu lại → lặp từ mục 3 (bước 1, 4, 6).

## Script
| Script | Việc |
|---|---|
| `match_info.py [CODE]` · `get_rules.py` · `get_state.py [--summary]` · `list_matches.py [--playing]` | Gọi API đọc |
| `probe_schema.py FILE --side S [--write]` · `extract_map.py FILE` | Dò schema, bản đồ |
| `analyze_state.py FILE --side S [--strategy N]...` | Bản đồ + quyết định của các chiến thuật (không gửi gì) |
| `run_strategy.py [CODE] --strategy N [--plan F] [--param k=v] [--dry-run --once]` | Vòng chơi tự động |
| `watch.py CODE --seconds 25` | Chờ + tóm tắt (vòng giám sát) |
| `send_plan.py` · `send_order.py` · `stop_tank.py` | Lệnh tay |
| `list_strategies.py` · `mock_server.py` · `bench.py` | Danh sách chiến thuật, server giả lập, so sánh offline |
