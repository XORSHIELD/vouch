"""
Server-level tests: security headers on every response, and the
auto-sync daemon wiring. In-process via starlette TestClient: no
ports, no network.

Run:  python test_server.py
"""

from __future__ import annotations

import os
import threading
import time

os.environ.pop("VOUCH_AUTO_SYNC", None)

from starlette.testclient import TestClient

import registry as R
import server

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("route wiring")
_routes: dict = {}
for _r in server.app.routes:
    _p = getattr(_r, "path", "")
    if _p:
        _routes[_p] = getattr(_r, "endpoint", None)
check("ask route runs ask()",
      _routes.get("/api/ask") is server.ask,
      repr(_routes.get("/api/ask")))
check("agent route runs agent_chat()",
      _routes.get("/api/agent/chat") is server.agent_chat,
      repr(_routes.get("/api/agent/chat")))


print("security headers")
client = TestClient(server.app, raise_server_exceptions=False)
r = client.get("/api/health")
check("health answers", r.status_code == 200, str(r.status_code))
check("nosniff set",
      r.headers.get("x-content-type-options") == "nosniff",
      str(dict(r.headers)))
check("framing denied to others",
      r.headers.get("x-frame-options") == "SAMEORIGIN",
      str(dict(r.headers)))
check("csp present",
      "default-src" in r.headers.get("content-security-policy", ""))
r = client.post("/api/demo/run", json={"key": "no-such-pack"})
check("404 carries the headers too",
      r.status_code == 404
      and r.headers.get("x-content-type-options") == "nosniff",
      str(r.status_code))

print("agent endpoint")
import agent as agent_mod
import ai as ai_mod
_old_keys = (agent_mod.API_KEY, ai_mod.API_KEY)
agent_mod.API_KEY = ""
ai_mod.API_KEY = ""
try:
    r = client.post("/api/agent/chat",
                    json={"message": "NAFDAC A99-9999, open a case"})
    body = r.json()
    check("chat answers keyless",
          r.status_code == 200 and len(body.get("answer", "")) > 20
          and body.get("provider") == "vouch-composer",
          f"{r.status_code} {str(body)[:200]}")
    check("high-risk chat proposes",
          (body.get("proposal") or {}).get("action") == "open_case",
          str(body.get("proposal"))[:200])
    r = client.post("/api/agent/chat",
                    json={"confirmed_action": {"action": "open_case",
                                               "pack": {}}})
    check("confirm needs sign-in", r.status_code == 401,
          str(r.status_code))
finally:
    agent_mod.API_KEY, ai_mod.API_KEY = _old_keys

print("auto-sync wiring")
os.environ["VOUCH_AUTO_SYNC"] = "1"
calls: list[str] = []
real_sync = R.sync_run
real_ready = R.mirror_ready


def _fake_sync(*a, **k):
    calls.append("sync")
    raise SystemExit  # stop the daemon loop silently after one call


R.sync_run = _fake_sync  # type: ignore
R.mirror_ready = lambda: False  # noqa: E731  force the boot-sync branch
try:
    server._maybe_auto_sync()
    deadline = time.time() + 10
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    check("enabled auto-sync calls sync_run", bool(calls))
finally:
    R.sync_run = real_sync
    R.mirror_ready = real_ready
    os.environ.pop("VOUCH_AUTO_SYNC", None)
time.sleep(0.2)  # let the dead daemon thread unwind
before = threading.active_count()
server._maybe_auto_sync()
check("disabled by default", threading.active_count() == before,
      f"{before} -> {threading.active_count()}")

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
