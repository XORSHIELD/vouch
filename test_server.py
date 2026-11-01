"""
Server-level tests: security headers on every response, and the
auto-sync daemon wiring. In-process via starlette TestClient: no
ports, no network.

Run:  python test_server.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import time

os.environ.pop("VOUCH_AUTO_SYNC", None)

TMP = tempfile.mkdtemp(prefix="vouch-server-")
os.environ["VOUCH_DATA_DIR"] = TMP
os.environ["VOUCH_AUTH_DB"] = os.path.join(TMP, "auth.sqlite")
os.environ["VOUCH_AUDIT_LOG"] = os.path.join(TMP, "audit.log")
os.environ["VOUCH_CACHE_DIR"] = os.path.join(TMP, "cache")

from starlette.testclient import TestClient
from starlette.requests import Request

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
check("whatsapp post runs wa_inbound()",
      _routes.get("/api/whatsapp/webhook") is server.wa_inbound,
      repr(_routes.get("/api/whatsapp/webhook")))
check("whatsapp verify wired",
      any(getattr(r, "endpoint", None) is server.wa_verify
          for r in server.app.routes))


print("csv export + audit")
server.auth_register(server.AuthBody(username="csvadmin", password="csvadmin-1"))
server.auth_register(server.AuthBody(username="csvexp", password="csvexp-csv-1"))
server.auth_register(server.AuthBody(username="csvother", password="csvother-1"))
_toka = server.auth_login(server.AuthBody(username="csvadmin", password="csvadmin-1"))["token"]
_tok = server.auth_login(server.AuthBody(username="csvexp", password="csvexp-csv-1"))["token"]
_tok2 = server.auth_login(server.AuthBody(username="csvother", password="csvother-1"))["token"]
_me = server.auth.me(_tok)
_me2 = server.auth.me(_tok2)
server.create_case(server.PackBody(product_name="CsvWidget"), _me)
server.create_case(server.PackBody(product_name="=cmd"), _me)
server.create_case(server.PackBody(product_name="OtherWidget"), _me2)
_body = server.export_cases(_me).body.decode()
check("csv has a header row", _body.startswith("id,created,band"), _body[:60])
check("csv scoped to opener",
      "CsvWidget" in _body and "OtherWidget" not in _body)
check("formula cells neutralised", "'=cmd" in _body, _body[:400])
check("download header set",
      "attachment" in server.export_cases(_me).headers.get("content-disposition", ""))
server.auth.set_role("csvother", "inspector")
check("staff export sees everything",
      "CsvWidget" in server.export_cases(server.auth.me(_tok2)).body.decode())
server.audit.log("probe-event", "csvexp", "x")
_evs = server.audit_log(50, server.auth.me(_toka))
check("audit lists recent events",
      any(e.get("event") == "probe-event" for e in _evs["events"]),
      str(_evs["events"][-1:]))
_deps = [getattr(d, "call", None) for r in server.app.routes
         if getattr(r, "path", "") == "/api/audit"
         for d in getattr(r.dependant, "dependencies", [])]
check("audit needs an admin", server.auth.require_admin in _deps)


print("backup restore")
_bdir = os.path.join(TMP, "backups")
os.makedirs(_bdir, exist_ok=True)
os.environ["VOUCH_BACKUP_DIR"] = _bdir
_blive = os.path.join(TMP, "livesrv")
_real_here, _real_sources = server.backup.HERE, server.backup.SOURCES
server.backup.HERE = _blive
server.backup.SOURCES = [os.path.join(_blive, ".data", "vouch.sqlite"),
                         os.path.join(_blive, ".data", "audit.log"),
                         os.path.join(_blive, "data", "vouch.sqlite"),
                         os.path.join(_blive, "data", "alerts.json"),
                         os.path.join(_blive, "data", "safety"),
                         os.path.join(_blive, ".cache", "registry.sqlite")]
try:
    os.makedirs(os.path.join(_blive, ".data"), exist_ok=True)
    _c = sqlite3.connect(os.path.join(_blive, ".data", "vouch.sqlite"))
    _c.execute("CREATE TABLE t (v TEXT)")
    _c.execute("INSERT INTO t VALUES ('srv-before')")
    _c.commit()
    _c.close()
    with open(os.path.join(_blive, ".data", "audit.log"), "w") as _fh:
        _fh.write("logline\n")
    _run = server.backup.backup_tree(server.backup.SOURCES, _bdir)["dest"]
    _c = sqlite3.connect(os.path.join(_blive, ".data", "vouch.sqlite"))
    _c.execute("UPDATE t SET v='srv-after'")
    _c.commit()
    _c.close()
    _name = os.path.basename(_run)
    _runs = server.list_backups(server.auth.me(_toka))["runs"]
    check("backups listed", any(r["name"] == _name for r in _runs),
          str(_runs))
    _out = server.restore_backup(server.BackupRestoreBody(run=_name),
                                 server.auth.me(_toka))
    _c = sqlite3.connect(os.path.join(_blive, ".data", "vouch.sqlite"))
    _v = _c.execute("SELECT v FROM t").fetchone()[0]
    _c.close()
    check("endpoint restores content", _v == "srv-before", _v)
    check("restart flagged", _out.get("restart_required") is True)
    try:
        server.restore_backup(server.BackupRestoreBody(run="../x"),
                              server.auth.me(_toka))
        check("path traversal refused", False)
    except Exception as exc:
        check("path traversal refused",
              "400" in str(exc) or "Unknown" in str(exc), str(exc)[:120])
    _bdeps = [getattr(d, "call", None) for r in server.app.routes
              if getattr(r, "path", "") in ("/api/backups",
                                            "/api/backup/restore")
              for d in getattr(r.dependant, "dependencies", [])]
    check("backup routes need an admin",
          _bdeps.count(server.auth.require_admin) == 2, str(len(_bdeps)))
finally:
    server.backup.HERE, server.backup.SOURCES = _real_here, _real_sources


print("whatsapp webhook")
os.environ["VOUCH_WA_VERIFY"] = "wa-verify"


def _get(query: bytes):
    return Request({"type": "http", "method": "GET",
                    "path": "/api/whatsapp/webhook",
                    "query_string": query, "headers": []})


_resp = server.wa_verify(_get(
    b"hub.mode=subscribe&hub.verify_token=wa-verify&hub.challenge=CH1"))
check("verify echoes challenge", _resp.body == b"CH1",
      repr(_resp.body))
try:
    server.wa_verify(_get(
        b"hub.mode=subscribe&hub.verify_token=nope&hub.challenge=CH1"))
    check("bad verify refused", False)
except Exception as exc:
    check("bad verify refused", "403" in str(exc), str(exc)[:80])


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
