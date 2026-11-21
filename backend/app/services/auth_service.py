from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.tenant_context import TenantContext


AUTH_DB_LOCK = threading.Lock()
_AUTH_SCHEMA_PATH: str | None = None
PASSWORD_ITERATIONS = 260_000
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]{3,64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth_database_file() -> Path:
    data_root = settings.resolve_local_path(settings.data_dir)
    data_root.mkdir(parents=True, exist_ok=True)
    return data_root / "auth.sqlite3"


def _connect() -> sqlite3.Connection:
    if (settings.db_backend or "sqlite").strip().lower() == "mysql":
        from app.services.mysql_compat import MySQLConnection

        return MySQLConnection(settings.mysql_auth_database)  # type: ignore[return-value]
    conn = sqlite3.connect(_auth_database_file(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def _session():
    """Close SQLite auth connections; its context manager only commits/rolls back."""
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        if isinstance(conn, sqlite3.Connection):
            conn.close()


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_company(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "company_id": str(row["company_id"]),
        "company_name": str(row["company_name"]),
        "status": str(row["status"]),
        "database_path": str(row["database_path"]),
        "upload_path": str(row["upload_path"]),
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }


def _row_user(row: sqlite3.Row | None, *, include_secret: bool = False) -> dict[str, Any] | None:
    if row is None:
        return None
    data: dict[str, Any] = {
        "user_id": int(row["user_id"]),
        "username": str(row["username"]),
        "display_name": str(row["display_name"] or row["username"]),
        "role": str(row["role"]),
        "company_id": str(row["company_id"]),
        "status": str(row["status"]),
        "last_login_at": row["last_login_at"],
        "create_time": row["create_time"],
        "update_time": row["update_time"],
    }
    if include_secret:
        data["password_hash"] = str(row["password_hash"])
    return data


def ensure_schema() -> None:
    global _AUTH_SCHEMA_PATH
    schema_path = str(_auth_database_file().resolve())
    if _AUTH_SCHEMA_PATH == schema_path:
        return
    with AUTH_DB_LOCK:
        if _AUTH_SCHEMA_PATH == schema_path:
            return
        with _session() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_company (
                    company_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
                    database_path TEXT NOT NULL,
                    upload_path TEXT NOT NULL,
                    create_time TEXT NOT NULL,
                    update_time TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_user (
                    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    role TEXT NOT NULL DEFAULT 'company_user' CHECK(role IN ('admin', 'company_user')),
                    company_id TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
                    last_login_at TEXT,
                    create_time TEXT NOT NULL,
                    update_time TEXT NOT NULL,
                    FOREIGN KEY(company_id) REFERENCES auth_company(company_id) ON DELETE RESTRICT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_session (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    create_time TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES auth_user(user_id) ON DELETE CASCADE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_user_company ON auth_user(company_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_session_user ON auth_session(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_session_expire ON auth_session(expires_at)")

            default_company = conn.execute("SELECT * FROM auth_company WHERE company_id = 'company_default'").fetchone()
            if default_company is None:
                now = _now()
                conn.execute(
                    "INSERT INTO auth_company (company_id, company_name, database_path, upload_path, create_time, update_time) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        "company_default",
                        "示例公司",
                        str(settings.database_file.resolve()),
                        str(settings.resolve_local_path(settings.data_dir) / "uploads"),
                        now,
                        now,
                    ),
                )
                default_company = conn.execute("SELECT * FROM auth_company WHERE company_id = 'company_default'").fetchone()

            has_admin = conn.execute("SELECT 1 FROM auth_user WHERE role = 'admin' LIMIT 1").fetchone()
            if has_admin is None:
                now = _now()
                username = _validate_username((settings.admin_initial_username or "admin").strip() or "admin")
                password = (settings.admin_initial_password or "").strip()
                if len(password) < 12:
                    raise ValueError("初始管理员密码至少需要 12 位")
                conn.execute(
                    "INSERT OR IGNORE INTO auth_user (username, password_hash, display_name, role, company_id, create_time, update_time) VALUES (?, ?, ?, 'admin', 'company_default', ?, ?)",
                    (username, _password_hash(password), "系统管理员", now, now),
                )
            conn.execute("DELETE FROM auth_session WHERE expires_at < ?", (_now(),))
            conn.commit()
        _AUTH_SCHEMA_PATH = schema_path


def _validate_username(username: str) -> str:
    value = username.strip()
    if not USERNAME_PATTERN.fullmatch(value):
        raise ValueError("账号需为 3-64 位字母、数字或 . _ @ - 字符")
    return value


def _load_user_with_company(conn: sqlite3.Connection, user_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    row = conn.execute(
        "SELECT u.*, c.company_name, c.status AS company_status, c.database_path, c.upload_path, c.create_time AS company_create_time, c.update_time AS company_update_time FROM auth_user u JOIN auth_company c ON c.company_id = u.company_id WHERE u.user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    user = _row_user(row) or {}
    company = {
        "company_id": user["company_id"],
        "company_name": row["company_name"],
        "status": row["company_status"],
        "database_path": row["database_path"],
        "upload_path": row["upload_path"],
        "create_time": row["company_create_time"],
        "update_time": row["company_update_time"],
    }
    return user, company


def _public_identity(user: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    return {"user": user, "company": {key: value for key, value in company.items() if key not in {"database_path", "upload_path"}}}


def login(username: str, password: str) -> dict[str, Any] | None:
    ensure_schema()
    with _session() as conn:
        row = conn.execute("SELECT * FROM auth_user WHERE username = ?", (username.strip(),)).fetchone()
        if row is None or row["status"] != "active" or not _password_matches(password, str(row["password_hash"])):
            return None
        user = _row_user(row) or {}
        company_row = conn.execute("SELECT * FROM auth_company WHERE company_id = ?", (user["company_id"],)).fetchone()
        company = _row_company(company_row)
        if company is None or company["status"] != "active":
            return None
        token = secrets.token_urlsafe(40)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=max(1, int(settings.auth_session_days or 7)))
        conn.execute("INSERT INTO auth_session (token_hash, user_id, expires_at, create_time, last_seen_at) VALUES (?, ?, ?, ?, ?)", (_token_hash(token), user["user_id"], expires.isoformat(), now.isoformat(), now.isoformat()))
        conn.execute("UPDATE auth_user SET last_login_at = ?, update_time = ? WHERE user_id = ?", (now.isoformat(), now.isoformat(), user["user_id"]))
        conn.commit()
        return {"access_token": token, "expires_at": expires.isoformat(), **_public_identity(user, company)}


def authenticate_token(token: str | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not token:
        return None
    ensure_schema()
    with _session() as conn:
        token_digest = _token_hash(token)
        row = conn.execute("SELECT user_id, expires_at, last_seen_at FROM auth_session WHERE token_hash = ?", (token_digest,)).fetchone()
        if row is None:
            return None
        try:
            if datetime.fromisoformat(str(row["expires_at"])) <= datetime.now(timezone.utc):
                conn.execute("DELETE FROM auth_session WHERE token_hash = ?", (_token_hash(token),))
                conn.commit()
                return None
        except ValueError:
            return None
        identity = _load_user_with_company(conn, int(row["user_id"]))
        if identity is None:
            return None
        user, company = identity
        if user["status"] != "active" or company["status"] != "active":
            return None
        now = datetime.now(timezone.utc)
        try:
            last_seen = datetime.fromisoformat(str(row["last_seen_at"]))
        except ValueError:
            last_seen = now - timedelta(minutes=10)
        if now - last_seen >= timedelta(minutes=5):
            conn.execute("UPDATE auth_session SET last_seen_at = ? WHERE token_hash = ?", (now.isoformat(), token_digest))
            conn.commit()
        return user, company


def logout(token: str | None) -> None:
    if not token:
        return
    ensure_schema()
    with _session() as conn:
        conn.execute("DELETE FROM auth_session WHERE token_hash = ?", (_token_hash(token),))
        conn.commit()


def build_tenant_context(user: dict[str, Any], company: dict[str, Any]) -> TenantContext:
    database_file = Path(company["database_path"]).resolve()
    upload_dir = Path(company["upload_path"]).resolve()
    database_file.parent.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    return TenantContext(
        user_id=int(user["user_id"]),
        username=str(user["username"]),
        role=str(user["role"]),
        company_id=str(company["company_id"]),
        company_name=str(company["company_name"]),
        database_file=database_file,
        upload_dir=upload_dir,
    )


def current_identity(user: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    return _public_identity(user, company)


def list_companies() -> list[dict[str, Any]]:
    ensure_schema()
    with _session() as conn:
        rows = conn.execute("SELECT c.*, (SELECT COUNT(1) FROM auth_user u WHERE u.company_id = c.company_id) AS user_count FROM auth_company c ORDER BY c.create_time DESC").fetchall()
    return [{**(_row_company(row) or {}), "user_count": int(row["user_count"] or 0)} for row in rows]


def create_company(name: str) -> dict[str, Any]:
    ensure_schema()
    company_name = name.strip()
    if not company_name or len(company_name) > 120:
        raise ValueError("公司名称不能为空且不能超过 120 个字符")
    company_id = f"company_{secrets.token_hex(8)}"
    now = _now()
    data_root = settings.resolve_local_path(settings.data_dir)
    company_root = data_root / "companies" / company_id
    database_file = company_root / "wordagent.sqlite3"
    upload_dir = data_root / "uploads" / "companies" / company_id
    with _session() as conn:
        try:
            conn.execute("INSERT INTO auth_company (company_id, company_name, database_path, upload_path, create_time, update_time) VALUES (?, ?, ?, ?, ?, ?)", (company_id, company_name, str(database_file), str(upload_dir), now, now))
        except sqlite3.IntegrityError as exc:
            raise ValueError("公司名称已存在") from exc
        conn.commit()
        row = conn.execute("SELECT * FROM auth_company WHERE company_id = ?", (company_id,)).fetchone()
    return _row_company(row) or {}


def list_users() -> list[dict[str, Any]]:
    ensure_schema()
    with _session() as conn:
        rows = conn.execute("SELECT u.*, c.company_name FROM auth_user u JOIN auth_company c ON c.company_id = u.company_id ORDER BY u.create_time DESC").fetchall()
    return [{**(_row_user(row) or {}), "company_name": row["company_name"]} for row in rows]


def create_user(*, username: str, password: str, display_name: str, company_id: str, role: str = "company_user") -> dict[str, Any]:
    ensure_schema()
    username = _validate_username(username)
    if len(password) < 12:
        raise ValueError("密码至少需要 12 位")
    if role not in {"company_user", "admin"}:
        raise ValueError("账号角色不合法")
    now = _now()
    with _session() as conn:
        if conn.execute("SELECT 1 FROM auth_company WHERE company_id = ? AND status = 'active'", (company_id,)).fetchone() is None:
            raise ValueError("公司不存在或已停用")
        try:
            conn.execute("INSERT INTO auth_user (username, password_hash, display_name, role, company_id, create_time, update_time) VALUES (?, ?, ?, ?, ?, ?, ?)", (username, _password_hash(password), display_name.strip()[:80] or username, role, company_id, now, now))
        except sqlite3.IntegrityError as exc:
            raise ValueError("账号已存在") from exc
        conn.commit()
        row = conn.execute("SELECT u.*, c.company_name FROM auth_user u JOIN auth_company c ON c.company_id = u.company_id WHERE u.username = ?", (username,)).fetchone()
    return {**(_row_user(row) or {}), "company_name": row["company_name"]}


def update_user(
    user_id: int,
    *,
    status: str | None = None,
    password: str | None = None,
    display_name: str | None = None,
    company_id: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    ensure_schema()
    updates: list[str] = []
    params: list[Any] = []
    if status is not None:
        if status not in {"active", "disabled"}:
            raise ValueError("账号状态不合法")
        updates.append("status = ?")
        params.append(status)
    if password is not None:
        if len(password) < 12:
            raise ValueError("密码至少需要 12 位")
        updates.append("password_hash = ?")
        params.append(_password_hash(password))
    if display_name is not None:
        updates.append("display_name = ?")
        params.append(display_name.strip()[:80])
    if company_id is not None:
        updates.append("company_id = ?")
        params.append(company_id)
    if role is not None:
        if role not in {"company_user", "admin"}:
            raise ValueError("账号角色不合法")
        updates.append("role = ?")
        params.append(role)
    if not updates:
        raise ValueError("没有需要更新的内容")
    updates.append("update_time = ?")
    params.append(_now())
    params.append(user_id)
    with _session() as conn:
        if company_id is not None and conn.execute("SELECT 1 FROM auth_company WHERE company_id = ? AND status = 'active'", (company_id,)).fetchone() is None:
            raise ValueError("公司不存在或已停用")
        cursor = conn.execute(f"UPDATE auth_user SET {', '.join(updates)} WHERE user_id = ?", params)
        if cursor.rowcount == 0:
            raise ValueError("账号不存在")
        if status is not None or password is not None or company_id is not None or role is not None:
            conn.execute("DELETE FROM auth_session WHERE user_id = ?", (user_id,))
        conn.commit()
        row = conn.execute("SELECT u.*, c.company_name FROM auth_user u JOIN auth_company c ON c.company_id = u.company_id WHERE u.user_id = ?", (user_id,)).fetchone()
    return {**(_row_user(row) or {}), "company_name": row["company_name"]}


def set_company_status(company_id: str, status: str) -> dict[str, Any]:
    ensure_schema()
    if status not in {"active", "disabled"}:
        raise ValueError("公司状态不合法")
    with _session() as conn:
        if company_id == "company_default" and status == "disabled":
            raise ValueError("默认公司不能停用")
        cursor = conn.execute("UPDATE auth_company SET status = ?, update_time = ? WHERE company_id = ?", (status, _now(), company_id))
        if cursor.rowcount == 0:
            raise ValueError("公司不存在")
        if status == "disabled":
            conn.execute("DELETE FROM auth_session WHERE user_id IN (SELECT user_id FROM auth_user WHERE company_id = ?)", (company_id,))
        conn.commit()
        row = conn.execute("SELECT c.*, (SELECT COUNT(1) FROM auth_user u WHERE u.company_id = c.company_id) AS user_count FROM auth_company c WHERE c.company_id = ?", (company_id,)).fetchone()
    return {**(_row_company(row) or {}), "user_count": int(row["user_count"] or 0)}
