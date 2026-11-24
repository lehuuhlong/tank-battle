# tank-battle — Agent + skill cho Battle City Online

Agent và skill cho Claude Code để chơi [Battle City Online](http://tank.recoff.cloud): chỉ huy 4 xe tăng qua HTTP API tới hết trận.
Có script Python (chỉ dùng thư viện chuẩn) để tìm đường cho xe 2×2, bắn khi thẳng hàng, và 8 chiến thuật có thể đổi giữa trận.

| Thành phần | Đường dẫn |
|---|---|
| Subagent `bco-commander` | `.claude/agents/bco-commander.md` |
| Skill `battle-city-online` | `.claude/skills/battle-city-online/SKILL.md` |
| Luật + API | `.claude/skills/battle-city-online/references/quick-reference.md` |
| Chiến thuật | `.claude/skills/battle-city-online/references/strategies.md` |
| Script | `.claude/skills/battle-city-online/scripts/` |

## Cài đặt
Cần **Python 3.9+** và Claude Code.

**Cách 1: dùng như một project.** Clone repo rồi mở Claude Code trong thư mục này. Skill và agent được nhận tự động.
```bash
git clone https://github.com/lehuuhlong/tank-battle
```
```bash
cd tank-battle
```

**Cách 2: cài cho mọi thư mục.** Chép nội dung `.claude/` vào `~/.claude/`.
```powershell
Copy-Item -Recurse -Force .\.claude\* "$HOME\.claude\"
```
```bash
cp -r .claude/* ~/.claude/
```

Đặt token (trong terminal sẽ mở Claude Code), rồi mở launcher và cho phép sẵn các lệnh cần dùng:
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
Agent dịch chiến thuật này thành `bco-data/<CODE>/plan.json`, gồm vai của từng xe và các luật đổi vai. Sau đó nó chạy vòng điều khiển ở chế độ nền và giám sát tới hết trận. Muốn chỉnh giữa trận: bấm **Esc**, gõ chỉ đạo mới, và kết thúc bằng "Tiếp tục chơi tới hết trận".

## Chiến thuật
| Tên | Ý chính |
|---|---|
| `scripted` | Làm theo `plan.json` dịch từ prompt của bạn |
| `adaptive` | Tự đổi vai theo giáp, máu nhà, điểm và thời gian (mặc định) |
| `siege` | Vây đại bàng địch, bắn lần lượt cách nhau hơn 5 giây |
| `rush` | Cả đội lao vào bắn đại bàng |
| `hunter` | Mỗi xe săn một xe địch |
| `fortress` | Giữ chốt quanh nhà |
| `collector` | Ưu tiên nhặt vật phẩm, ⭐ trước |
| `balanced` | Tự xếp vai theo thứ tự xe |

Các vai: `siege`, `siege_left`, `siege_right`, `siege_side`, `siege_front`, `rush`, `stage`, `hunt`, `defend`, `guard_front:N`, `collect`, `hold`, và hậu tố `<vai>+collect`.

## Trận đầu tiên với server thật
Tên các trường trong `state` chưa được điền sẵn; script tự dò theo các tên thường gặp. Ở trận đầu (nên dùng phòng Luyện tập với bot), agent sẽ chạy `probe_schema.py`, đối chiếu với mục **Tài liệu API** trên trang Hướng dẫn, rồi điền vào `references/state-schema.json`. Chỉ cần làm một lần.

## Thử offline
Không cần server thật:
```bash
cd .claude/skills/battle-city-online/scripts
```
```bash
python bench.py
```
```bash
python analyze_state.py fixtures/sample_state.json --side bottom
```
`bench.py` so sánh các chiến thuật trên server giả lập (`mock_server.py`). Server này chỉ dùng để kiểm tra code chạy đúng, không phải engine thật của game.
