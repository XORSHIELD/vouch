"""
Backup tests. Copies fixtures through backup_tree and checks rotation.

Run:  python test_backup.py
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import time

import backup

fails = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"   {detail}" if detail and not ok else ""))
    if not ok:
        fails += 1


TMP = tempfile.mkdtemp(prefix="vouch-backup-")
SRC = os.path.join(TMP, "src")
os.makedirs(os.path.join(SRC, "data", "safety"))
db = os.path.join(SRC, "data", "vouch.sqlite")
con = sqlite3.connect(db)
con.execute("CREATE TABLE t (x TEXT)")
con.execute("INSERT INTO t VALUES ('hello')")
con.commit()
con.close()
photo = os.path.join(SRC, "data", "safety", "rpt_1.jpg")
with open(photo, "wb") as fh:
    fh.write(b"\xff\xd8\xff" + b"0" * 100)
DEST = os.path.join(TMP, "backups")
os.makedirs(DEST)

print("backup")
out = backup.backup_tree([db, photo], DEST, keep=7)
check("run lands in a timestamped folder",
      os.path.isdir(out["dest"]), str(out))
check("both sources reported copied",
      len(out["copied"]) == 2 and not out["missing"], str(out))
check("everything reported copied is on disk",
      all(os.path.exists(os.path.join(out["dest"], rel))
          for rel in out["copied"]),
      str(os.listdir(out["dest"])))
db_rels = [rel for rel in out["copied"] if rel.endswith("vouch.sqlite")]
if db_rels:
    got_db = os.path.join(out["dest"], db_rels[0])
    probe = sqlite3.connect(got_db)
    try:
        row = probe.execute("SELECT x FROM t").fetchone()
    finally:
        probe.close()  # unclosed handle locks the file on Windows
    check("sqlite copy is consistent", row[0] == "hello")
else:
    check("sqlite copy is consistent", False, str(out))
out2 = backup.backup_tree([db, os.path.join(SRC, "nope.sqlite")],
                          DEST, keep=1)
check("missing source reported, not fatal",
      len(out2["missing"]) == 1, str(out2))
backup.backup_tree([db], DEST, keep=1)
check("rotation keeps one run", len(os.listdir(DEST)) == 1,
      str(os.listdir(DEST)))

print(f"\n{'FAILED' if fails else 'all checks passed'} ({fails} failures)")
raise SystemExit(1 if fails else 0)
