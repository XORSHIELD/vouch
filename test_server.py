"""
Server HTTP tests: security headers on every response, the agent and
auth endpoints over HTTP. In-process via starlette TestClient: no
ports, no network — but it does need socket permission, which some
sandboxes forbid. Everything provable without sockets lives in
test_server_unit.py instead.

Run:  python test_server.py
"""

from __future__ import annotations

import os
import tempfile

os.environ.pop("VOUCH_AUTO_SYNC", None)

TMP = tempfile.mkdtemp(prefix="vouch-server-")
os.environ["VOUCH_DATA_DIR"] = TMP
os.environ["VOUCH_AUTH_DB"] = os.path.join(TMP, "auth.sqlite")
os.environ["VOUCH_AUDIT_LOG"] = os.path.join(TMP, "audit.log")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")

from starlette.testclient import TestClient

import server

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


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

print("auth over HTTP")
r = client.post("/api/auth/register",
                json={"username": "httpuser", "password": "httpuser-h1",
                      "email": "http@example.com"})
check("register with email",
      r.status_code == 200
      and r.json()["user"]["email"] == "http@example.com"
      and r.json()["user"]["role"] == "admin",
      f"{r.status_code} {r.text[:200]}")
r = client.post("/api/auth/login",
                json={"username": "http@example.com",
                      "password": "httpuser-h1"})
tok = (r.json() if r.status_code == 200 else {}).get("token", "")
check("login with the email", bool(tok),
      f"{r.status_code} {r.text[:200]}")
r = client.get("/api/auth/me", headers={"Authorization": "Bearer " + tok})
check("me resolves the username",
      r.json().get("user", {}).get("username") == "httpuser",
      r.text[:200])
r = client.post("/api/auth/register",
                json={"username": "httpuser2", "password": "httpuser-h2",
                      "email": "http@example.com"})
check("duplicate email refused", r.status_code == 400,
      f"{r.status_code} {r.text[:200]}")

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
