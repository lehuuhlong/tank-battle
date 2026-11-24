# Cài agent + skill Battle City Online lên máy bạn

## 1. Cần có
- **Python 3.9+** (không cần cài thư viện nào). Kiểm tra: `python --version` (Windows) / `python3 --version`.
  Chưa có thì cài từ python.org, nhớ tick "Add python.exe to PATH".
- Claude Code (launcher của team) và token game (nút **Cài đặt agent** trong phòng).

## 2. Giải nén vào thư mục `.claude` của bạn
File zip có sẵn cấu trúc `agents/` và `skills/`.

PowerShell:
```powershell
Expand-Archive .\bco-agent-skill.zip -DestinationPath "$HOME\.claude" -Force
```
Git Bash / macOS / Linux:
```bash
unzip -o bco-agent-skill.zip -d ~/.claude
```
Kết quả:
```
~/.claude/agents/bco-commander.md
~/.claude/skills/battle-city-online/SKILL.md, references/, scripts/
```

## 3. Đặt token (trong terminal sẽ mở Claude Code)
```powershell
$env:BCO_TOKEN = "token-của-bạn"
```
```bash
export BCO_TOKEN=token-của-bạn
```
Kiểm tra nhanh (bỏ trống mã = trận đang playing):
```bash
python ~/.claude/skills/battle-city-online/scripts/list_matches.py
```

## 4. Mở Claude Code, cho phép sẵn lệnh để agent không hỏi quyền giữa trận
```powershell
claude-launcher-<TeamNo>.exe --allowedTools "Bash(curl:*)" "Bash(python:*)" "Bash(python3:*)" "Bash(py:*)"
```
(Agent còn ghi `bco-data/<CODE>/plan.json` bằng Write/Edit; muốn khỏi hỏi thì thêm `"Write" "Edit"` — tuỳ bạn.)
Mở **session mới** sau khi giải nén để Claude Code nhận skill.

## 5. Prompt đầu trận
Gõ **trước** khi bấm Sẵn sàng (agent gọi API → chấm "agent" sáng → bấm được Sẵn sàng):
```
Dùng skill battle-city-online. PHÒNG: ABCD.
Chiến thuật trận này:
- Xe 1 và Xe 2 vào hông trái căn cứ địch, bắn lần lượt.
- Xe 3 săn xe địch gần nhất, gặp sao gần đường thì ghé nhặt.
- Xe 4 chốt trước căn cứ mình 3 ô, nòng hướng về phía địch.
- 3 phút đầu (đại bàng còn giáp): xe 1-3 săn xe địch và nhặt sao.
- Căn cứ mình còn 2 máu: Xe 3 về giữ nhà cùng Xe 4.
- Còn dưới 60 giây mà đang dẫn điểm: cả bốn xe về giữ nhà.
Chơi tới khi trận kết thúc.
```
Không muốn tự nghĩ chiến thuật: `Dùng skill battle-city-online, PHÒNG ABCD, chiến thuật adaptive. Chơi tới khi trận kết thúc.`
Hoặc giao cho subagent: `Dùng agent bco-commander chơi phòng ABCD ...`

Chỉnh giữa trận: **Esc** → gõ chỉ đạo → kết thúc bằng "Tiếp tục chơi tới hết trận". Xe vẫn đánh trong lúc bạn gõ
(vòng điều khiển chạy nền).

## 6. Trận đầu tiên với server thật
Tên trường của `state` chưa được điền sẵn (script tự dò theo các tên thường gặp). Ở trận đầu (tốt nhất là phòng **Luyện tập với bot**),
agent sẽ chạy `probe_schema.py` và đối chiếu mục **Tài liệu API** trên trang Hướng dẫn để điền
`skills/battle-city-online/references/state-schema.json`. Làm một lần là xong cho các trận sau.
Có thể nói thẳng: `Dùng skill battle-city-online, PHÒNG ABCD: dò và điền schema state trước, rồi chơi adaptive tới hết trận.`

## 7. Thử offline không cần server
```bash
cd ~/.claude/skills/battle-city-online/scripts
python bench.py                 # so sánh các chiến thuật trên server giả lập
python analyze_state.py fixtures/sample_state.json --side bottom
```
