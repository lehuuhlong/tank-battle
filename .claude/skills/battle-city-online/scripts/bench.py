#!/usr/bin/env python3
"""So sánh chiến thuật trên server giả lập (mock_server.py). Mỗi chiến thuật chạy 1 trận rút gọn.

  python3 bench.py                         # tất cả chiến thuật
  python3 bench.py siege hunter --speed 6 --game-seconds 200 --seeds 1,2
Kết quả mock chỉ để kiểm tra chiến thuật chạy đúng / so sánh thô — không phải engine thật.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import strategies

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("names", nargs="*")
ap.add_argument("--speed", type=float, default=6)
ap.add_argument("--game-seconds", type=float, default=200)
ap.add_argument("--armor", type=float, default=30)
ap.add_argument("--seeds", default="1")
ap.add_argument("--param", action="append", default=[])
a = ap.parse_args()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


names = a.names or sorted(strategies.load_all())
print(f"{'chiến thuật':12s} {'seed':>4s} {'điểm ta':>8s} {'điểm địch':>9s} {'nhà ta':>6s} {'nhà địch':>8s}  kết quả")
for name in names:
    for seed in a.seeds.split(","):
        port = free_port()
        data = tempfile.mkdtemp(prefix="bco-bench-")
        mock = subprocess.Popen([sys.executable, os.path.join(HERE, "mock_server.py"), "--port", str(port), "--speed", str(a.speed),
                                 "--armor", str(a.armor), "--game-seconds", str(a.game_seconds), "--seed", seed],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        env = dict(os.environ, BCO_TOKEN="test", BCO_BASE_URL=f"http://127.0.0.1:{port}/api/v1", BCO_DATA_DIR=data)
        cmd = [sys.executable, os.path.join(HERE, "run_strategy.py"), "TEST1", "--strategy", name, "--interval", str(max(0.1, 1.2 / a.speed)),
               "--refresh", "1.5", "--quiet", "--max-minutes", str(a.game_seconds * 2 / a.speed / 60 + 0.5)]
        for p in a.param:
            cmd += ["--param", p]
        subprocess.run(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        last = None
        try:
            for line in open(os.path.join(data, "TEST1", "log.jsonl")):
                last = json.loads(line)
            st = json.load(open(os.path.join(data, "TEST1", "state-latest.json")))
            ms, es = st["you"]["score"], st["enemy"]["score"]
            mb, eb = st["you"]["base_hp"], st["enemy"]["base_hp"]
            res = "THẮNG" if (eb <= 0 or ms >= 20000 or (mb > 0 and ms > es)) else "THUA"
            print(f"{name:12s} {seed:>4s} {ms:8d} {es:9d} {mb:6d} {eb:8d}  {res}")
        except (FileNotFoundError, KeyError, TypeError) as e:
            print(f"{name:12s} {seed:>4s} lỗi: {e} (last={bool(last)})")
        mock.terminate()
