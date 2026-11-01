"""
Vouch — accounts and sessions.

Username-or-email + password, stdlib only: PBKDF2-SHA256 for passwords, opaque bearer
tokens for sessions. Roles are consumer < inspector < admin.

  * consumers run checks and own cases
  * inspectors also read every case and the queue
  * admins also seed/reset demos and manage roles

First run: the first account registered is the admin. Set
VOUCH_ADMIN_PASSWORD before starting the server to create the admin
non-interactively instead.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time

from fastapi import HTTPException, Request

import audit

DB_PATH = (os.environ.get("VOUCH_AUTH_DB")
           or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".data", "vouch.sqlite"))

ROLES = ("consumer", "inspector", "admin")
SESSION_DAYS = 7
_ITERS = 210_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY,
  passhash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'consumer',
  email TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token_sha TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


def _db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    con.executescript(SCHEMA)
    cols = [r[1] for r in con.execute("PRAGMA table_info(users)")]
    if "email" not in cols:
        con.execute(
            "ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email"
                " ON users(email) WHERE email <> ''")
    con.commit()
    return con


def _hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, _ITERS).hex()
    return f"pbkdf2${_ITERS}${salt.hex()}${digest}"


def _check(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$", 3)
        cand = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt), int(iters)).hex()
    except Exception:
        return False
    return secrets.compare_digest(cand, digest)


def _valid_username(username: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9._-]{2,31}", username or ""))


def _valid_email(email: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""))


def register(username: str, password: str, email: str = "") -> dict:
    """Create an account. The first account is the admin. ValueError if bad.

    The email is optional; when given it becomes a second way to sign in.
    """
    username = (username or "").strip().lower()
    email = (email or "").strip().lower()
    if not _valid_username(username):
        raise ValueError("Username: 3-32 chars, letters, digits, . _ -")
    if email and not _valid_email(email):
        raise ValueError("That email address does not look right.")
    if len(password or "") < 8:
        raise ValueError("Password needs at least 8 characters.")
    con = _db()
    try:
        if con.execute("SELECT 1 FROM users WHERE username = ?",
                       (username,)).fetchone():
            raise ValueError("That username is taken.")
        if email and con.execute("SELECT 1 FROM users WHERE email = ?",
                                 (email,)).fetchone():
            raise ValueError("That email is already registered.")
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        role = "admin" if users == 0 else "consumer"
        con.execute(
            "INSERT INTO users (username, passhash, role, email, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (username, _hash(password), role, email, time.time()))
        con.commit()
    finally:
        con.close()
    audit.log("user_registered", username, role)
    return {"username": username, "role": role, "email": email}


def ensure_admin() -> None:
    """Create the admin account on first run, from the environment."""
    password = os.environ.get("VOUCH_ADMIN_PASSWORD") or ""
    con = _db()
    try:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    finally:
        con.close()
    if users:
        return
    if not password:
        print("auth: no users yet and no VOUCH_ADMIN_PASSWORD."
              " Register a consumer, or set VOUCH_ADMIN_PASSWORD and"
              " restart to create the admin.")
        return
    if len(password) < 8:
        print("auth: VOUCH_ADMIN_PASSWORD is shorter than 8 characters;"
              " refusing to create the admin.")
        return
    con = _db()
    try:
        con.execute(
            "INSERT INTO users (username, passhash, role, created_at)"
            " VALUES ('admin', ?, 'admin', ?)",
            (_hash(password), time.time()))
        con.commit()
    finally:
        con.close()
    audit.log("admin_created", "admin", "first run")
    print("auth: admin account created.")


def login(identifier: str, password: str) -> str | None:
    """A session token, or None when the credentials are wrong.

    The identifier is the username or the email; both match
    case-insensitively and resolve to the same account.
    """
    identifier = (identifier or "").strip().lower()
    if not identifier:
        return None
    con = _db()
    try:
        row = con.execute(
            "SELECT username, passhash FROM users"
            " WHERE username = ? OR (email = ? AND email <> '')",
            (identifier, identifier)).fetchone()
        if not row or not _check(password or "", row["passhash"]):
            return None
        username = row["username"]
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        con.execute("DELETE FROM sessions WHERE expires_at < ?",
                    (time.time(),))
        con.execute(
            "INSERT INTO sessions (token_sha, username, expires_at)"
            " VALUES (?, ?, ?)",
            (digest, username, time.time() + SESSION_DAYS * 86400))
        con.commit()
    finally:
        con.close()
    audit.log("login", username, "")
    return token


def logout(token: str) -> None:
    if not token:
        return
    digest = hashlib.sha256(token.encode()).hexdigest()
    con = _db()
    try:
        con.execute("DELETE FROM sessions WHERE token_sha = ?",
                    (digest,))
        con.commit()
    finally:
        con.close()


def me(token: str) -> dict | None:
    """The user behind a token, or None when it is missing or expired."""
    if not token:
        return None
    digest = hashlib.sha256(token.encode()).hexdigest()
    con = _db()
    try:
        row = con.execute(
            "SELECT u.username, u.role, u.email, s.expires_at FROM sessions s"
            " JOIN users u ON u.username = s.username"
            " WHERE s.token_sha = ?", (digest,)).fetchone()
        if not row:
            return None
        if row["expires_at"] < time.time():
            con.execute("DELETE FROM sessions WHERE token_sha = ?",
                        (digest,))
            con.commit()
            return None
        return {"username": row["username"], "role": row["role"],
                "email": row["email"] or ""}
    finally:
        con.close()


def set_role(username: str, role: str) -> bool:
    if role not in ROLES:
        return False
    con = _db()
    try:
        cur = con.execute("UPDATE users SET role = ? WHERE username = ?",
                          (role, (username or "").strip().lower()))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    scheme, _, token = auth.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def _level(role: str) -> int:
    return ROLES.index(role) if role in ROLES else -1


def _require(role: str, request: Request) -> dict:
    user = me(bearer_token(request))
    if not user:
        raise HTTPException(401, "Sign in first.")
    if _level(user["role"]) < _level(role):
        raise HTTPException(403, "That needs a higher role.")
    return user


def require_user(request: Request) -> dict:
    return _require("consumer", request)


def require_admin(request: Request) -> dict:
    return _require("admin", request)
