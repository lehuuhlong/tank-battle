# tank-battle — Agent + skill cho Battle City Online

Agent và skill cho Claude Code để chơi [Battle City Online](http://tank.recoff.cloud) qua HTTP API, chỉ huy 4 xe tăng tới hết trận.
Script viết bằng Python, chỉ dùng thư viện chuẩn. Có sẵn tìm đường cho xe 2×2, bắn khi thẳng hàng, và 8 chiến thuật có thể đổi giữa trận.

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
Gõ prompt **trước** khi bấm Sẵn sàng. Agent phải gọi API cho phòng thì nút Sẵn sàng mới bật.
```
Dùng skill battle-city-online. PHÒNG: ABCD.
Chiến thuật trận này:
- Xe 1 và Xe 2 vào hông trái căn cứ địch, bắn lần lượt.
- Xe 3 săn xe địch gần nhất, gặp sao gần đường thì ghé nhặt.
- Xe 4 chốt trước căn cứ mình 3 ô, nòng hướng về phía địch.
- 3 phút đầu: xe 1-3 săn xe và nhặt sao.
- Căn cứ mình còn 2 máu: Xe 3 về giữ nhà cùng Xe 4.
- Còn dưới 60 giây mà đang dẫn điểm: cả bốn xe về giữ nhà.
Chơi tới khi trận kết thúc.
```
Agent dịch phần chiến thuật thành `bco-data/<CODE>/plan.json`, chạy vòng điều khiển ở chế độ nền và giám sát tới hết trận.

Muốn chỉnh giữa trận: bấm **Esc**, gõ chỉ đạo mới, kết thúc bằng câu "Tiếp tục chơi tới hết trận".

Không muốn tự nghĩ chiến thuật thì dùng: `Dùng skill battle-city-online, PHÒNG ABCD, chiến thuật adaptive. Chơi tới khi trận kết thúc.`

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
