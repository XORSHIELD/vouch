"""
Vouch — backups.

Copies the live databases and uploads into a timestamped folder and keeps
the newest few. SQLite files are copied with the online backup API, so a
backup taken while the server runs is still consistent.

Run:  python backup.py
      python backup.py --keep 14 --dest D:\\vouch-backups
      python backup.py --restore backups/20261101-120000
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES = [
    os.path.join(HERE, ".data", "vouch.sqlite"),
    os.path.join(HERE, ".data", "audit.log"),
    os.path.join(HERE, "data", "vouch.sqlite"),
    os.path.join(HERE, "data", "alerts.json"),
    os.path.join(HERE, "data", "safety"),
    os.path.join(HERE, ".cache", "registry.sqlite"),
]


def _remove_with_retry(path: str, tries: int = 5) -> None:
    # Windows can briefly lock a just-written file (indexer, AV scan);
    # two backups in the same second would otherwise collide.
    last: OSError | None = None
    for _ in range(tries):
        try:
            os.remove(path)
            return
        except PermissionError as exc:
            last = exc
            time.sleep(0.05)
    assert last is not None
    raise last


def _copy_sqlite(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        _remove_with_retry(dst)
    src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=10)
    try:
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()


def _unique_dest(dest_root: str, stamp: str) -> str:
    # Two runs in the same second must never share (and clobber) one folder.
    dest = os.path.join(dest_root, stamp)
    n = 2
    while os.path.exists(dest):
        dest = os.path.join(dest_root, f"{stamp}-{n}")
        n += 1
    return dest


def _rmtree_loud(path: str, tries: int = 10) -> None:
    # Rotation retries transient Windows locks, then warns loudly. A leaked
    # old run costs disk; a silent ignore_errors hides it forever.
    last: OSError | None = None
    for _ in range(tries):
        try:
            shutil.rmtree(path)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.1)
    print(f"backup: could not prune {path}: {last}", file=sys.stderr)


def backup_tree(sources: list[str], dest_root: str, keep: int = 7) -> dict:
    """Copy sources into dest_root/<timestamp>/, prune to `keep` runs."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    dest = _unique_dest(dest_root, stamp)
    copied: list[str] = []
    missing: list[str] = []
    for src in sources:
        if not os.path.exists(src):
            missing.append(src)
            continue
        rel = os.path.relpath(src, HERE) if src.startswith(HERE) else \
            os.path.basename(src.rstrip(os.sep))
        target = os.path.join(dest, rel)
        if os.path.isdir(src):
            shutil.copytree(src, target)
        elif src.endswith(".sqlite"):
            _copy_sqlite(src, target)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(src, target)
        copied.append(rel)
    runs = sorted(d for d in os.listdir(dest_root)
                  if os.path.isdir(os.path.join(dest_root, d)))
    for old in runs[:-max(1, keep)]:
        _rmtree_loud(os.path.join(dest_root, old))
    return {"dest": dest, "copied": copied, "missing": missing,
            "kept": len(os.listdir(dest_root))}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Back up the Vouch databases")
    ap.add_argument("--dest", default=os.path.join(HERE, "backups"))
    ap.add_argument("--keep", type=int, default=7)
    ap.add_argument("--restore", metavar="RUN", default="",
                    help="restore RUN (a backups/<stamp> folder) over the live data")
    a = ap.parse_args(argv)
    os.makedirs(a.dest, exist_ok=True)
    try:
        if a.restore:
            return _restore_cli(a.restore)
        out = backup_tree(SOURCES, a.dest, a.keep)
    except Exception as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1
    print(f"backup -> {out['dest']}")
    for name in out["copied"]:
        print(f"  kept {name}")
    for name in out["missing"]:
        print(f"  skip {name} (not present)")
    return 0


def rel_paths(sources: list[str]) -> list[str]:
    """Backup-relative path for each source (mirrors backup_tree)."""
    return [os.path.relpath(s, HERE) if s.startswith(HERE) else
            os.path.basename(s.rstrip(os.sep)) for s in sources]


def _check_sqlite(path: str) -> None:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError(
                f"backup file is not a readable database: {path}") from exc
    finally:
        con.close()
    if len(rows) != 1 or str(rows[0][0]).lower() != "ok":
        raise ValueError(f"backup file failed its integrity check: {path}")


def _clear_sidecars(path: str) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        side = path + suffix
        if os.path.exists(side):
            try:
                os.remove(side)
            except OSError as exc:
                raise OSError(
                    f"cannot clear {side} (is the server still running? "
                    f"stop it first): {exc}") from exc


def _copy_live(src: str, dst: str) -> None:
    if os.path.isdir(src):
        shutil.copytree(src, dst)
    elif src.endswith(".sqlite"):
        _copy_sqlite(src, dst)
    else:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def restore_tree(run_dir: str, live_root: str | None = None,
                 sources: list[str] | None = None,
                 snap_root: str | None = None) -> dict:
    """Restore a backup run over the live tree.

    Every .sqlite in the run is integrity-checked BEFORE anything live is
    touched. Current live files are snapshotted to a sibling
    pre-restore-<stamp> folder first. Only recognized backup paths are
    written, always under live_root. Stop the server before restoring a
    live system; restart it after.
    """
    live = live_root or HERE
    srcs = sources if sources is not None else SOURCES
    if not os.path.isdir(run_dir):
        raise FileNotFoundError(f"not a Vouch backup run: {run_dir}")
    rels = rel_paths(srcs)
    present = [r for r in rels if os.path.exists(os.path.join(run_dir, r))]
    if not present:
        raise ValueError(
            f"not a Vouch backup run (no recognized files): {run_dir}")
    for rel in present:
        if rel.endswith(".sqlite"):
            _check_sqlite(os.path.join(run_dir, rel))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    snap = _unique_dest(snap_root or os.path.dirname(os.path.abspath(run_dir)),
                        f"pre-restore-{stamp}")
    os.makedirs(snap, exist_ok=True)
    snap_copied = []
    for rel in present:
        cur = os.path.join(live, rel)
        if os.path.exists(cur) or os.path.islink(cur):
            _copy_live(cur, os.path.join(snap, rel))
            snap_copied.append(rel)
    restored = []
    for rel in present:
        src = os.path.join(run_dir, rel)
        dst = os.path.join(live, rel)
        if os.path.isdir(src) and not os.path.islink(src):
            tmp = dst + ".restoring"
            if os.path.exists(tmp):
                _rmtree_loud(tmp)
            shutil.copytree(src, tmp, symlinks=True)
            if os.path.isdir(dst) and not os.path.islink(dst):
                shutil.rmtree(dst)
            elif os.path.exists(dst) or os.path.islink(dst):
                os.remove(dst)
            os.rename(tmp, dst)
        else:
            tmp = dst + ".restoring"
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if src.endswith(".sqlite"):
                if os.path.exists(tmp):
                    _remove_with_retry(tmp)
                _copy_sqlite(src, tmp)
            else:
                shutil.copy2(src, tmp)
            if dst.endswith(".sqlite"):
                _clear_sidecars(dst)
            os.replace(tmp, dst)
        restored.append(rel)
    return {"restored": restored, "snapshot": snap,
            "snapshotted": snap_copied,
            "skipped": [r for r in rels if r not in present]}


def _restore_cli(run: str) -> int:
    try:
        out = restore_tree(run)
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"restore <- {run}")
    for name in out["restored"]:
        print(f"  back {name}")
    print(f"  snapshot {out['snapshot']} (pre-restore copy)")
    print("  restart the server before use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
