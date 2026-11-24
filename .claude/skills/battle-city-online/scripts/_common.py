"""Helpers shared by the CLI scripts."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bco_lib.client import Client  # noqa: E402

DATA_DIR = os.environ.get("BCO_DATA_DIR", os.path.join(os.getcwd(), "bco-data"))


def match_dir(code):
    d = os.path.join(DATA_DIR, str(code))
    os.makedirs(d, exist_ok=True)
    return d


def save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2) if not isinstance(obj, str) else obj)


def match_list(resp):
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        for k in ("matches", "data", "items", "results"):
            if isinstance(resp.get(k), list):
                return resp[k]
    return []


def match_code(m):
    for k in ("code", "id", "match_code", "slug"):
        if isinstance(m, dict) and m.get(k) is not None:
            return str(m[k])
    return None


def resolve_code(client, code=None):
    """Use the given code, or the single match with playing=true."""
    if code:
        return code
    playing = [m for m in match_list(client.matches()) if isinstance(m, dict) and m.get("playing")]
    if not playing:
        raise SystemExit("Không có trận nào đang playing=true. Truyền mã trận.")
    if len(playing) > 1:
        print("Nhiều trận đang chơi, lấy trận đầu:", [match_code(m) for m in playing], file=sys.stderr)
    return match_code(playing[0])


def parse_params(pairs):
    out = {}
    for p in pairs or []:
        k, _, v = p.partition("=")
        out[k.strip()] = v.strip()
    return out


def cached_side(code):
    p = os.path.join(DATA_DIR, str(code), "side.txt")
    return open(p).read().strip() if os.path.exists(p) else None
