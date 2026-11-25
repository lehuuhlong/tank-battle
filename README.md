# tank-battle — Agent + skill cho Battle City Online

Agent và skill cho Claude Code để chơi [Battle City Online](http://tank.recoff.cloud) qua HTTP API, chỉ huy 4 xe tăng tới hết trận.
Script viết bằng Python, chỉ dùng thư viện chuẩn. Vòng điều khiển chạy mỗi 0,5 giây: lấy state và bản đồ, rồi gửi một `/plan` cho cả đội.
Có sẵn tìm đường cho xe 2×2, phản xạ (bắn ngay, bắn đón, né đạn), và 8 chiến thuật có thể đổi giữa trận.

```
.claude/agents/bco-commander.md                 subagent chỉ huy trận
.claude/skills/battle-city-online/
  SKILL.md                                      quy trình cho agent
  references/rules.md                           luật, API, vai và chiến thuật
  references/state-schema.json                  ánh xạ các trường của state (điền ở trận đầu)
  scripts/bco.py                                CLI: info, rules, state, probe, analyze, run, watch, plan, order, stop
  scripts/bco_core.py                           HTTP client, bản đồ, tìm đường, đường đạn, chuẩn hoá state
  scripts/bco_brain.py                          tactics, các chiến thuật, runner gửi lệnh
server/                                         server luyện tập tự dựng (xem mục cuối)
  app.py                                        HTTP API giống server thật + web + phòng + bot
  engine.py                                     engine mô phỏng luật game, bản đồ, bot Dễ/Vừa/Khó
  static/index.html                             giao diện web
```

## Cài đặt
Cần **Python 3.9+** và Claude Code.

Có hai cách:
- **Dùng như project:** clone repo rồi mở Claude Code trong thư mục `tank-battle`.
- **Dùng ở mọi thư mục:** chép nội dung `.claude/` vào `~/.claude/` (lệnh thứ hai bên dưới).

```bash
git clone https://github.com/lehuuhlong/tank-battle
```
```powershell
Copy-Item -Recurse -Force .\tank-battle\.claude\* "$HOME\.claude\"
```

Đặt token, rồi mở Claude Code với quyền chạy lệnh được cấp sẵn để agent không phải hỏi giữa trận:
```powershell
$env:BCO_TOKEN = "token-của-bạn"
```
```powershell
claude-launcher-<TeamNo>.exe --allowedTools "Bash(curl:*)" "Bash(python:*)" "Bash(python3:*)" "Bash(py:*)"
```

## Prompt đầu trận
Gõ prompt **trước** khi bấm Sẵn sàng. Agent phải gọi API cho phòng thì nút Sẵn sàng mới bật. Thay `ABCD` bằng mã phòng của bạn.

### Prompt mẫu đầy đủ
Mỗi ý trong prompt khớp với một vai hoặc một điều kiện mà skill hiểu, nên agent dịch sang `plan.json` mà không phải đoán.
```
Dùng skill battle-city-online. PHÒNG: ABCD.

Chiến thuật trận này (dịch thành plan.json, chạy scripted):

Đội hình chính (sau khi hết giáp):
- Xe 1: vây đại bàng địch từ hông trái.
- Xe 2: vây đại bàng địch từ hông phải. Xe 1 và Xe 2 bắn lần lượt, cách nhau hơn 5 giây.
- Xe 3: săn xe địch gần nhất, gặp vật phẩm gần đường thì ghé nhặt.
- Xe 4: chốt trước căn cứ mình 3 ô, nòng hướng về phía địch; địch lao về thì ra chặn.

3 phút đầu (đại bàng còn giáp):
- Xe 1, 2, 3 săn xe địch và nhặt vật phẩm, ưu tiên sao. Xe 4 vẫn chốt nhà.
- Xe nào đủ 3 sao thì đi phá căn cứ địch ngay, không chờ hết giáp.

Đổi vai theo tình huống (luật sau ghi đè luật trước):
- Căn cứ mình vừa bị bắn: Xe 4 ra chặn xe đang bắn nhà.
- Căn cứ mình còn 2 máu trở xuống: Xe 3 giữ hông trái, Xe 4 giữ hông phải.
- Xe nào còn 3 mạng trở xuống: về giữ nhà (giữ mạng để thắng khi hoà điểm).
- Đại bàng địch còn 2 máu trở xuống: Xe 3 cũng đi vây, hông nào gần thì đánh hông đó.
- Còn dưới 60 giây đồng hồ game mà đang dẫn điểm: cả 4 xe về giữ nhà.
- Còn dưới 90 giây đồng hồ game mà đang thua điểm: cả 4 xe lao vào bắn căn cứ địch, không cần xếp lượt.

Lưu ý:
- Không bắn vỡ tường quanh căn cứ mình.
- Ưu tiên nhặt sao và xẻng.
- Gặp hàng rào đạn (2 xe địch quay mặt vào nhau) thì không đi xuyên, đánh xe rào từ phía nó không quay nòng.

Chơi tới khi trận kết thúc. Không dừng, không hỏi lại giữa trận. Mỗi vòng in một dòng trạng thái.
```

### Agent dịch thành `plan.json`
Bạn có thể dùng để kiểm tra agent dịch đúng không. File nằm ở `bco-data/<CODE>/plan.json`; sửa giữa trận thì có hiệu lực ngay ở nhịp sau.
```json
{
  "roles": {"1": "siege_left", "2": "siege_right", "3": "hunt+collect", "4": "guard_front:3"},
  "rules": [
    {"when": {"armor": true}, "roles": {"1": "hunt+collect", "2": "hunt+collect", "3": "hunt+collect"}},
    {"when": {"armor": true, "tank_stars_gte": 3}, "roles": {"1": "siege_left", "2": "siege_right", "3": "siege_side"}},
    {"when": {"my_base_hit": true}, "roles": {"4": "defend"}},
    {"when": {"my_base_hp_lte": 2}, "roles": {"3": "guard_left", "4": "guard_right"}},
    {"when": {"tank_lives_lte": 3}, "roles": {"*": "defend"}},
    {"when": {"enemy_base_hp_lte": 2}, "roles": {"3": "siege_side"}},
    {"when": {"time_left_lte": 60, "lead_gte": 1}, "roles": {"*": "defend"}},
    {"when": {"time_left_lte": 90, "lead_lte": -1}, "roles": {"*": "rush"}}
  ]
}
```

### Những phần chạy tự động, không cần ghi vào prompt
Với mọi chiến thuật, runner luôn tự làm:
- **Phản xạ mỗi nhịp 0,5 giây:** bắn ngay khi địch thẳng hàng, bắn đón khi địch sắp cắt ngang làn, né khi bị nhắm mà chưa bắn trả được.
- **Cứu nhà:** xe địch nào có đường đạn tới đại bàng ta thì xe ta gần nhất bắn nó hoặc lao vào chắn làn.
- **Tình huống khó:** tự tránh hàng rào đạn, tự đi vòng khi kẹt đường, phá gạch dưới vật phẩm để nhặt.

### Chỉnh giữa trận
Bấm **Esc**, gõ chỉ đạo mới, và luôn kết thúc bằng câu "Tiếp tục chơi tới hết trận". Trong lúc bạn gõ, xe vẫn đánh vì vòng điều khiển chạy nền.
```
Địch dồn cả hai hông. Xe 3 giữ hông trái, Xe 4 giữ hông phải, Xe 1 và 2 tiếp tục vây.
Tiếp tục chơi tới hết trận.
```

### Bản ngắn
Khi không muốn tự nghĩ chiến thuật:
```
Dùng skill battle-city-online. PHÒNG: ABCD. Chiến thuật adaptive.
Chơi tới khi trận kết thúc. Không dừng, không hỏi lại giữa trận.
```

## Chiến thuật
| Tên | Ý chính |
|---|---|
| `scripted` | Làm theo `plan.json` dịch từ prompt của bạn |
| `adaptive` | Tự đổi vai theo giáp, máu nhà, điểm, thời gian (mặc định) |
| `siege` | Vây đại bàng địch, bắn lần lượt cách nhau hơn 5 giây |
| `rush` | Cả đội lao vào bắn đại bàng |
| `hunter` | Mỗi xe săn một xe địch |
| `fortress` | Giữ chốt quanh nhà |
| `collector` | Ưu tiên nhặt vật phẩm, ⭐ trước |
| `balanced` | Tự xếp vai theo thứ tự xe |

## Định dạng state của server thật
`references/state-schema.json` đã điền theo JSON thật của `GET /state?map=true` (`time_left_s`, `bases_open_in_s`,
`you`/`opponent`, `bases.yours`/`bases.opponent`, `powerups`, `events`…), xem bảng trong `references/rules.md`.
Nếu server đổi định dạng, chạy `bco.py probe <file state>` để xem trường nào không còn khớp.

## Server luyện tập (tự dựng)
Trong giải đấu, phòng luyện tập trên server thật bị đóng. Thư mục `server/` là một bản mô phỏng chạy trên máy bạn: cùng HTTP API
(`/api/v1/matches/...`), cùng luật trong trang hướng dẫn, có bot Dễ/Vừa/Khó, có giao diện web để xem trận. Agent và skill chơi ở đây
không cần sửa gì, chỉ cần đổi địa chỉ API.

**Đây không phải engine chính thức.** Những gì tài liệu không nói rõ (tên trường của state, định dạng phản hồi, tốc độ xe/đạn, 30 bản đồ…)
được làm theo cách hợp lý nhất; xem "Khác biệt có thể có" bên dưới.

### Chạy
Cần Python 3.9+, không cài thêm gì.
```powershell
python server\app.py
```
Mở http://localhost:8080, nhập tên để đăng nhập. Muốn đồng đội trong mạng LAN vào cùng (đấu người với người):
```powershell
python server\app.py --host 0.0.0.0 --port 8080
```
Đồng đội mở `http://<IP máy bạn>:8080`.

### Chơi thử một trận
1. **Tạo phòng**: chọn Bot Dễ/Vừa/Khó, hoặc "Người" rồi đồng đội bấm Join. Muốn test nhanh: chọn **Tua nhanh ×4**, hoặc giảm thời gian trận / giáp.
2. Trong phòng bấm **Cài đặt agent**: copy lệnh đặt biến môi trường (có `BCO_TOKEN` và `BCO_BASE_URL` trỏ về server này) vào terminal sẽ mở Claude Code.
3. Gõ prompt có mã phòng như bình thường. Khi agent gọi API, chấm "agent" sáng xanh → bấm **Sẵn sàng** → trận bắt đầu sau 5 giây.
4. Xem trận trực tiếp; **Nhật ký agent** cho thấy mọi lần agent gọi API. Hết trận có bảng kết quả: số xe hạ, bị hạ, vật phẩm,
   số lần trúng nhà địch, **số lần bắn trúng nhà mình**, và **số giây đứng không việc** của từng xe. Chủ phòng bấm **Đấu lại**.

Chạy thẳng runner của skill, không qua Claude Code:
```powershell
$env:BCO_BASE_URL = "http://localhost:8080/api/v1"
```
```powershell
python .claude\skills\battle-city-online\scripts\bco.py run ABCD --strategy adaptive
```

### Có gì
- **API:** `GET /matches`, `GET /matches/{code}` (nhận `X-Agent-Model`), `/rules`, `/state?map=true`, `POST /plan` / `/orders` / `/stop`.
  Lệnh sai chỉ từ chối xe đó. Trận chưa bắt đầu trả `409`. Quá 15 lần gọi/giây trả `429`. Sai mã phòng thì báo kèm mã phòng hiện tại.
- **Luật:** xe 2×2; lệnh hai bên áp dụng cùng lúc ở đầu nhịp 0,5 giây; tối đa 5 bước chờ; giáp 3:00 (tường quanh đại bàng là thép, xe 3 sao bắn vỡ);
  chỉ mất máu khi bị bắn từ nửa sân của căn cứ đó; trúng đại bàng địch thì xe bị đưa về nhà; đại bàng bất tử 5 giây; hồi sinh 3 giây có khiên;
  vật phẩm theo cặp đối xứng mỗi 20 giây (⭐ ⛑️ ⛏️ 🛡️, có thể nằm trên gạch); đạn gặp nhau triệt tiêu; đạn trúng gì tính nấy, kể cả nhà mình;
  4 điều kiện thắng và chuỗi xét khi bằng điểm như tài liệu.
- **Bản đồ ASCII** vẽ đè như server thật: `1-4` xe bạn, `O` xe địch, `H` nhà bạn, `T` nhà địch, `P` vật phẩm.
- **Bản đồ:** 2 bản đồ thật (`san-1`, `san-2`) + bản đồ ngẫu nhiên đối xứng 180°.
- **Bot:** Dễ (cả đội giữ nhà, nghĩ 4 giây), Vừa (nửa công nửa thủ, 2 giây), Khó (như Vừa + đánh xe gặp trên đường, chặn mọi xe vào sân nhà,
  nhặt đồ gần đường, nhà còn 2 máu thì rút về thủ, 0,1 giây). Bot ra lệnh qua đúng các lệnh `move` / `move_fire` / `turn` / `fire` / `stop`.
- **Cấu hình phòng** (chủ phòng, trước trận): tua nhanh, thời gian trận, giáp, số xe, số mạng, máu căn cứ, mốc điểm, tốc độ xe/đạn, bản đồ.
  Tài khoản và token lưu ở `server/data/users.json` (đã nằm trong `.gitignore`).

### Đo sức mạnh skill trên server này
Đo bằng runner của skill (`adaptive`) đấu bot, trận đầy đủ 8:00 đồng hồ game (tua nhanh ×4):

| Đối thủ | Thắng | Hạ / bị hạ |
|---|---|---|
| Bot Khó, mốc 20000 điểm | 7/8 | 1,55 |
| Bot Vừa, mốc 20000 điểm | 4/4 | 3,4 |
| Bot Khó, mốc 100000 điểm (phải phá nhà hoặc hết giờ) | 8/8 | 1,33 |

Chạy trên server này đã bắt được và sửa các lỗi: tự bắn trúng nhà mình, tự phá tường nhà mình bằng `move_fire`,
lệnh bị từ chối vì hàng chờ bị dồn đôi.

### Khác biệt có thể có so với server thật
API, state, phản hồi `/plan` / `/orders` / `/stop`, thông báo lỗi và trạng thái (`scheduled` / `preparing` / `live` / `finished`)
làm **theo đặc tả và JSON mẫu của server thật**. Các phần còn đoán, chỉnh được trong `server/engine.py` (lớp `Config` và hàm
`state_for`): chi tiết sự kiện `order_failed`, các trường của `bullets[]` ngoài `x`,`y`, tốc độ xe (3 ô/s) và đạn (9 ô/s, 1 sao 13,5 ô/s), cách xe xử lý khi bị xe khác chắn
(chờ 1 giây rồi báo lỗi), hiệu ứng băng, vị trí xuất hiện vật phẩm, giới hạn 15 lần gọi/giây, và 28 bản đồ còn lại.

