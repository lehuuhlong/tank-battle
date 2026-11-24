"""Minimal HTTP client for the Battle City Online API.

Config (env):
  BCO_TOKEN        required, bearer token
  BCO_BASE_URL     default http://tank.recoff.cloud/api/v1
  BCO_AGENT_MODEL  value for the X-Agent-Model header (the model actually playing)
"""
import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "http://tank.recoff.cloud/api/v1"


class ApiError(Exception):
    def __init__(self, status, body, url):
        self.status, self.body, self.url = status, body, url
        super().__init__(f"HTTP {status} {url}: {body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:500]}")


class Client:
    def __init__(self, token=None, base_url=None, model=None, timeout=10, retries=3):
        self.token = token or os.environ.get("BCO_TOKEN")
        if not self.token:
            raise SystemExit("BCO_TOKEN chưa được đặt (export BCO_TOKEN=...)")
        self.base = (base_url or os.environ.get("BCO_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("BCO_AGENT_MODEL")
        self.timeout = timeout
        self.retries = retries

    # -- low level -------------------------------------------------------
    def request(self, method, path, body=None, params=None):
        url = self.base + path
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if self.model:
            headers["X-Agent-Model"] = self.model
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        last = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return _decode(r.read())
            except urllib.error.HTTPError as e:
                payload = _decode(e.read())
                if e.code == 429:  # rate limited: back off and retry
                    last = ApiError(e.code, payload, url)
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    time.sleep(float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 1.0 * (attempt + 1))
                    continue
                # 4xx are answers (e.g. rejected orders): do not retry, surface them
                if 400 <= e.code < 500:
                    raise ApiError(e.code, payload, url)
                last = ApiError(e.code, payload, url)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = e
            time.sleep(0.3 * (attempt + 1))
        raise last

    # -- endpoints -------------------------------------------------------
    def matches(self):
        return self.request("GET", "/matches")

    def match(self, code):
        return self.request("GET", f"/matches/{code}")

    def rules(self, code):
        return self.request("GET", f"/matches/{code}/rules")

    def state(self, code, with_map=True):
        return self.request("GET", f"/matches/{code}/state", params={"map": "true"} if with_map else None)

    def plan(self, code, steps):
        return self.request("POST", f"/matches/{code}/plan", {"steps": steps})

    def order(self, code, step):
        return self.request("POST", f"/matches/{code}/orders", step)

    def stop(self, code, tank):
        return self.request("POST", f"/matches/{code}/stop", {"tank": tank})


def _decode(raw):
    text = raw.decode("utf-8", "replace") if raw else ""
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        return text


def find_rejections(response, sent_tanks):
    """Best-effort: return {tank_id: reason} for tanks the server rejected in a /plan response.

    The response format is not documented, so we look for any dict that names a tank and
    carries an error/reason/ok=false/accepted=false. Unknown formats -> {} (log the raw reply).
    """
    out = {}

    def visit(node):
        if isinstance(node, dict):
            tank = node.get("tank", node.get("tank_id", node.get("id")))
            failed = (
                node.get("ok") is False
                or node.get("accepted") is False
                or node.get("success") is False
                or str(node.get("status", "")).lower() in ("rejected", "error", "failed")
                or ("error" in node and node["error"])
            )
            if tank is not None and failed:
                out[_tank_key(tank)] = node.get("error") or node.get("reason") or node.get("message") or node
            for k, v in node.items():
                if k in ("rejected", "errors", "failed") and isinstance(v, dict):
                    for tk, reason in v.items():
                        out[_tank_key(tk)] = reason
                visit(v)
        elif isinstance(node, list):
            for v in node:
                visit(v)

    visit(response)
    return {k: v for k, v in out.items() if k in {_tank_key(t) for t in sent_tanks}} or out


def _tank_key(t):
    try:
        return int(t)
    except (TypeError, ValueError):
        return t
