"""
Hardening tests: accounts, rate limits, audit log.

No server, no network: auth runs against a temporary database.

Run:  python test_auth.py
"""

from __future__ import annotations

import os
import tempfile

TMP = tempfile.mkdtemp(prefix="vouch-auth-")
os.environ["VOUCH_AUTH_DB"] = os.path.join(TMP, "auth.sqlite")
os.environ["VOUCH_AUDIT_LOG"] = os.path.join(TMP, "audit.log")

import audit
import auth
from ratelimit import Limiter

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


print("accounts")
first = auth.register("owner", "owner-owner-1")
check("first account is admin", first["role"] == "admin", str(first))
try:
    auth.register("jo", "short")
    check("short password refused", False)
except ValueError:
    check("short password refused", True)
try:
    auth.register("bad name!", "long-enough-1")
    check("bad username refused", False)
except ValueError:
    check("bad username refused", True)
try:
    auth.register("owner", "another-long-1")
    check("duplicate refused", False)
except ValueError:
    check("duplicate refused", True)
auth.register("clerk", "clerk-clerk-1")

print("sessions")
token = auth.login("owner", "owner-owner-1")
check("login returns a token", bool(token))
check("wrong password fails", auth.login("owner", "nope-nope-1") is None)
check("unknown user fails", auth.login("ghost", "ghost-ghost-1") is None)
who = auth.me(token or "")
check("token resolves", (who or {}).get("username") == "owner", str(who))
check("bogus token fails", auth.me("bogus") is None)
auth.logout(token or "")
check("logout kills the token", auth.me(token or "") is None)

print("roles")
check("set_role works", auth.set_role("clerk", "inspector") is True)
check("bad role refused", auth.set_role("clerk", "root") is False)
check("unknown user refused", auth.set_role("ghost", "admin") is False)

print("rate limits")
lim = Limiter()
path = "/api/check"
ok_run = all(lim.allow("1.2.3.4", path, now=1000.0 + i * 0.01)
             for i in range(300))
check("300 check requests pass", ok_run)
check("301st is refused", lim.allow("1.2.3.4", path, now=1003.0) is False)
check("another client unaffected",
      lim.allow("5.6.7.8", path, now=1003.0) is True)
check("window slides",
      lim.allow("1.2.3.4", path, now=1100.0) is True)
auth_path = "/api/auth/login"
for i in range(20):
    lim.allow("9.9.9.9", auth_path, now=2000.0 + i * 0.01)
check("auth bucket is tighter",
      lim.allow("9.9.9.9", auth_path, now=2001.0) is False)
agent_path = "/api/agent/chat"
for i in range(60):
    lim.allow("7.7.7.7", agent_path, now=3000.0 + i * 0.01)
check("agent bucket matches ask",
      lim.allow("7.7.7.7", agent_path, now=3001.0) is False)

print("audit log")
audit.log("test_event", "tester", "hello")
recent = audit.recent(5)
check("event recorded",
      any(r.get("event") == "test_event" for r in recent), str(recent))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
