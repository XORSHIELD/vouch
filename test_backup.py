"""test_backup.py: backup runs and the restore path.

Everything runs against a scratch tree (backup.HERE and backup.SOURCES
patched), never the live repo files.

Run:  python test_backup.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import backup

TMP = tempfile.mkdtemp(prefix="vouch-backup-")
LIVE = os.path.join(TMP, "live")
BACKUPS = os.path.join(TMP, "backups")
os.makedirs(BACKUPS, exist_ok=True)
backup.HERE = LIVE
backup.SOURCES = [os.path.join(LIVE, ".data", "vouch.sqlite"),
                  os.path.join(LIVE, ".data", "audit.log"),
                  os.path.join(LIVE, "data", "vouch.sqlite"),
                  os.path.join(LIVE, "data", "alerts.json"),
                  os.path.join(LIVE, "data", "safety"),
                  os.path.join(LIVE, ".cache", "registry.sqlite")]

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


def _seed(tag: str) -> None:
    for db in (os.path.join(LIVE, ".data", "vouch.sqlite"),
               os.path.join(LIVE, "data", "vouch.sqlite"),
               os.path.join(LIVE, ".cache", "registry.sqlite")):
        os.makedirs(os.path.dirname(db), exist_ok=True)
        if os.path.exists(db):
            os.remove(db)
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE t (v TEXT)")
        con.execute("INSERT INTO t VALUES (?)", (tag,))
        con.commit()
        con.close()
    for rel, body in ((".data/audit.log", f"audit {tag}\n"),
                      ("data/alerts.json", f'{{"tag": "{tag}"}}'),
                      ("data/safety/note.txt", f"safety {tag}\n")):
        p = os.path.join(LIVE, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(body)


def _read(db: str) -> str:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return con.execute("SELECT v FROM t").fetchone()[0]
    finally:
        con.close()


print("backup run")
_seed("before")
out = backup.backup_tree(backup.SOURCES, BACKUPS)
RUN = out["dest"]
check("all six sources copied",
      len(out["copied"]) == 6 and not out["missing"],
      str(out))
check("run keeps the live layout",
      os.path.isfile(os.path.join(RUN, ".data", "vouch.sqlite"))
      and os.path.isfile(os.path.join(RUN, "data", "safety", "note.txt")))

print("restore")
_seed("after")
open(os.path.join(LIVE, ".data", "vouch.sqlite-wal"), "w").write("x")
open(os.path.join(LIVE, "data", "vouch.sqlite-shm"), "w").write("x")
res = backup.restore_tree(RUN)
_LIVE_MAIN = os.path.join(LIVE, ".data", "vouch.sqlite")
check("sqlite rows restored",
      _read(_LIVE_MAIN) == "before"
      and _read(os.path.join(LIVE, "data", "vouch.sqlite")) == "before"
      and _read(os.path.join(LIVE, ".cache", "registry.sqlite")) == "before")
check("plain files restored",
      open(os.path.join(LIVE, ".data", "audit.log")).read() == "audit before\n"
      and open(os.path.join(LIVE, "data", "safety", "note.txt")).read()
      == "safety before\n")
check("wal sidecars cleared",
      not os.path.exists(os.path.join(LIVE, ".data", "vouch.sqlite-wal"))
      and not os.path.exists(os.path.join(LIVE, "data", "vouch.sqlite-shm")))
check("snapshot kept the pre-restore state",
      _read(os.path.join(res["snapshot"], ".data", "vouch.sqlite")) == "after"
      and os.path.basename(res["snapshot"]).startswith("pre-restore-"),
      res["snapshot"])
check("restore reports its files",
      len(res["restored"]) == 6, str(res["restored"]))

print("extras ignored")
open(os.path.join(RUN, "weird.exe"), "w").write("x")
_seed("again")
res2 = backup.restore_tree(RUN)
check("unknown files skipped",
      "weird.exe" not in str(res2["restored"])
      and not os.path.exists(os.path.join(LIVE, "weird.exe")))

print("cli")
_seed("cli-after")
check("cli restore exits 0", backup.main(["--restore", RUN]) == 0)
check("cli restore brings content back", _read(_LIVE_MAIN) == "before")
check("cli missing run exits 1",
      backup.main(["--restore", os.path.join(TMP, "nope")]) == 1)

print("restore guards")
with open(os.path.join(RUN, ".data", "vouch.sqlite"), "r+b") as fh:
    fh.seek(20)
    fh.write(b"XX")
try:
    backup.restore_tree(RUN)
    check("corrupt backup refused", False)
except ValueError:
    check("corrupt backup refused", _read(_LIVE_MAIN) == "before")
_empty = os.path.join(TMP, "empty")
os.makedirs(_empty, exist_ok=True)
try:
    backup.restore_tree(_empty)
    check("run without recognized files refused", False)
except ValueError:
    check("run without recognized files refused", True)
try:
    backup.restore_tree(os.path.join(TMP, "missing"))
    check("missing run refused", False)
except FileNotFoundError:
    check("missing run refused", True)

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
