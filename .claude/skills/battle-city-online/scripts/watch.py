#!/usr/bin/env python3
"""Chờ tối đa N giây trong khi run_strategy.py đang chạy, rồi in tóm tắt ngắn của trận.

Dùng trong vòng giám sát của agent (thay cho sleep): gọi lặp lại tới khi in "TRẬN KẾT THÚC".
Thoát sớm khi có chuyện đáng xử lý: nhà ta mất máu, lệnh bị từ chối liên tục, cảnh báo schema, trận kết thúc.

  watch.py CODE [--seconds 25]
Mã thoát: 0 = trận còn tiếp · 3 = trận kết thúc · 4 = runner không chạy (log đứng yên).
"""
import argparse
import json
import os
import time

from _common import DATA_DIR

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("code")
ap.add_argument("--seconds", type=float, default=25)
a = ap.parse_args()

d = os.path.join(DATA_DIR, a.code)
log_path, state_path = os.path.join(d, "log.jsonl"), os.path.join(d, "state-latest.json")


def read_from(pos):
    recs = []
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            f.seek(pos)
            for line in f:
                try:
                    recs.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
            pos = f.tell()
    return recs, pos


start_pos = os.path.getsize(log_path) if os.path.exists(log_path) else 0
pos, recs, t_end, alerts = start_pos, [], time.time() + a.seconds, []
first = None
while time.time() < t_end:
    new, pos = read_from(pos)
    recs += new
    if recs and first is None:
        first = recs[0]
    for r in new:
        if first and r["base_hp"][0] is not None and first["base_hp"][0] is not None and r["base_hp"][0] < first["base_hp"][0]:
            alerts.append(f"NHÀ TA MẤT MÁU: {first['base_hp'][0]}→{r['base_hp'][0]}")
            first = r
        if r.get("rejected"):
            alerts.append(f"bị từ chối: {r['rejected']}")
        if str(r.get("status", "")).lower() in ("finished", "ended", "over"):
            alerts.append("finished")
    if any(x.startswith("NHÀ TA") for x in alerts) or sum(x.startswith("bị từ chối") for x in alerts) >= 3:
        break
    time.sleep(1)

state = None
try:
    state = json.load(open(state_path, encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    pass
st = str((state or {}).get("status", "")).lower()
finished = st in ("finished", "ended", "over", "done") or "finished" in alerts
last = recs[-1] if recs else None

if finished:
    print(f"TRẬN KẾT THÚC · trạng thái cuối: {json.dumps({k: (state or {}).get(k) for k in ('status', 'winner', 'result', 'reason') if (state or {}).get(k) is not None}, ensure_ascii=False)}")
if last:
    jobs = " | ".join(f"T{k}:{v.get('job', '').split('|')[-1][:28]}" for k, v in sorted(last["decisions"].items()))
    print(f"t={last['time_left']} giáp={'Y' if last['armor'] else 'N'} điểm {last['score'][0]}-{last['score'][1]} "
          f"nhà {last['base_hp'][0]}-{last['base_hp'][1]} {('phase=' + last['phase']) if last.get('phase') else ''} "
          f"chiến thuật={last['strategy']} · {len(recs)} nhịp/{a.seconds:.0f}s")
    print("  xe ta:", last["tanks"], " địch:", last["enemies"])
    print("  việc:", jobs)
    for w in sorted(set(last.get("warnings") or [])):
        print("  ! cảnh báo:", w)
for x in dict.fromkeys(alerts):
    if x != "finished":
        print("  !", x)
if not finished and not recs:
    fresh = os.path.exists(state_path) and time.time() - os.path.getmtime(state_path) < 10
    if fresh:  # runner alive but match not live yet
        if st in ("live", "playing", "running"):
            print("trận vừa bắt đầu — gọi watch lại")
        else:
            print(f"chờ trận bắt đầu (status={st or '?'}) — người dùng cần bấm Sẵn sàng trên trang phòng")
        raise SystemExit(0)
    print("! không có nhịp mới — run_strategy.py có đang chạy không? (xem output của nó, chạy lại nếu đã chết)")
    raise SystemExit(4)
raise SystemExit(3 if finished else 0)
