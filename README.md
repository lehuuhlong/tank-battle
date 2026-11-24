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

## Trận đầu tiên với server thật
Tên các trường trong `state` chưa được điền sẵn; script đang tự dò theo các tên thường gặp. Ở trận đầu, tốt nhất là phòng Luyện tập với bot:
1. agent chạy `bco.py probe`;
2. đối chiếu với mục **Tài liệu API** trên trang Hướng dẫn của game;
3. điền vào `references/state-schema.json`.

Chỉ cần làm một lần.
