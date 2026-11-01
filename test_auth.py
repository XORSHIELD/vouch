"""
Hardening tests: accounts, rate limits, audit log.

No server, no network: auth runs against a temporary database.

Run:  python test_auth.py
"""

from __future__ import annotations

import os
import sqlite3
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

print("email sign-in")
bob = auth.register("bob", "bob-bob-bob-1", email="Bob@Example.com")
check("register accepts an email", bob.get("email") == "bob@example.com",
      str(bob))
tok = auth.login("bob@example.com", "bob-bob-bob-1")
check("login works with the email", bool(tok))
check("email login is case-insensitive",
      bool(auth.login("BOB@EXAMPLE.COM", "bob-bob-bob-1")))
who2 = auth.me(tok or "")
check("email login resolves the username",
      (who2 or {}).get("username") == "bob", str(who2))
check("username login still works",
      bool(auth.login("bob", "bob-bob-bob-1")))
check("unknown email fails",
      auth.login("nobody@example.com", "bob-bob-bob-1") is None)
try:
    auth.register("bobby", "bobby-bobby-1", email="bob@example.com")
    check("duplicate email refused", False)
except ValueError:
    check("duplicate email refused", True)
try:
    auth.register("bobby", "bobby-bobby-1", email="BOB@example.com")
    check("duplicate email refused regardless of case", False)
except ValueError:
    check("duplicate email refused regardless of case", True)
for i, bad in enumerate(("not-an-email", "a@b", "@example.com",
                         "bob@", "bob@exa mple.com")):
    try:
        auth.register(f"ebad{i}", "ebad-ebad-1", email=bad)
        check(f"bad email refused ({bad!r})", False)
    except ValueError:
        check(f"bad email refused ({bad!r})", True)
auth.register("noemail", "noemail-no-1")
check("register still works without an email",
      bool(auth.login("noemail", "noemail-no-1")))

print("email migration")
old_db = os.path.join(TMP, "legacy.sqlite")
_legacy = sqlite3.connect(old_db)
_legacy.executescript(
    "CREATE TABLE users (username TEXT PRIMARY KEY,"
    " passhash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'consumer',"
    " created_at REAL NOT NULL);"
    "CREATE TABLE sessions (token_sha TEXT PRIMARY KEY,"
    " username TEXT NOT NULL, expires_at REAL NOT NULL);"
    "INSERT INTO users (username, passhash, role, created_at)"
    " VALUES ('oldtimer', '" + auth._hash("oldtimer-old-1") + "',"
    " 'consumer', 0);")
_legacy.commit()
_legacy.close()
_real_db = auth.DB_PATH
auth.DB_PATH = old_db
try:
    check("pre-migration user can log in",
          bool(auth.login("oldtimer", "oldtimer-old-1")))
    _cols = [r[1] for r in sqlite3.connect(old_db).execute(
        "PRAGMA table_info(users)").fetchall()]
    check("email column added", "email" in _cols, str(_cols))
    check("register works on a migrated database",
          auth.register("newbie", "newbie-new-1",
                        email="newbie@example.com")["email"]
          == "newbie@example.com")
finally:
    auth.DB_PATH = _real_db

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
