from __future__ import annotations

"""Small MySQL adapter for the existing SQLite-shaped repository.

The application intentionally keeps its existing ``?`` placeholders and row
access pattern.  This adapter translates the small SQLite dialect surface the
repository uses, so the migration can be enabled without rewriting every
service in one release.
"""

import re
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from typing import Any

import pymysql
from pymysql.cursors import Cursor

from app.core.config import settings


class CompatRow:
    def __init__(self, values: Sequence[Any], keys: Sequence[str]):
        self._values = tuple(values)
        self._keys = tuple(keys)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def keys(self):
        return self._mapping.keys()

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


class CompatCursor:
    def __init__(self, cursor: Cursor):
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def fetchone(self) -> CompatRow | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return _row_from_dict(row, self._cursor.description)

    def fetchall(self) -> list[CompatRow]:
        return [_row_from_dict(row, self._cursor.description) for row in self._cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


def _row_from_dict(row: dict[str, Any], description: Sequence[Sequence[Any]] | None) -> CompatRow:
    keys = [str(item[0]) for item in (description or [])]
    return CompatRow([row.get(key) for key in keys], keys)


def _replace_qmarks(sql: str) -> str:
    """Replace SQLite placeholders without touching quoted string literals."""
    result: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(sql):
        char = sql[index]
        if quote:
            # PyMySQL applies Python percent interpolation whenever execute()
            # receives parameters. Escape literal percent signs even inside
            # quoted SQL values (for example LIKE '%.docx').
            result.append("%%" if char == "%" else char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
            result.append(char)
        elif char == "?":
            result.append("%s")
        elif char == "%":
            result.append("%%")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _translate_sql(sql: str) -> str:
    stripped = sql.strip()
    if not stripped:
        return sql
    upper = stripped.upper()
    table_info = re.match(r"^\s*PRAGMA\s+table_info\s*\(\s*([A-Za-z0-9_]+)\s*\)\s*;?\s*$", sql, re.I)
    if table_info:
        table_name = table_info.group(1)
        return (
            "SELECT ORDINAL_POSITION - 1 AS cid, COLUMN_NAME AS name, COLUMN_TYPE AS type, "
            "CASE WHEN IS_NULLABLE = 'NO' THEN 1 ELSE 0 END AS notnull, "
            "COLUMN_DEFAULT AS dflt_value, CASE WHEN COLUMN_KEY = 'PRI' THEN 1 ELSE 0 END AS pk "
            "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            f"AND TABLE_NAME = '{table_name}' ORDER BY ORDINAL_POSITION"
        )
    if upper.startswith("PRAGMA"):
        return "SELECT 1 AS pragma_noop"
    if upper.startswith("INSERT OR IGNORE"):
        sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE", "INSERT IGNORE", sql, flags=re.IGNORECASE)
    if re.match(r"^\s*CREATE\s+TABLE\b", sql, re.IGNORECASE):
        sql = _mysqlize_create_table(sql)
    # SQLite schema upgrades add TEXT columns after table creation. MySQL
    # requires non-NULL TEXT defaults to be parenthesized expressions.
    if re.match(r"^\s*ALTER\s+TABLE\s+\S+\s+ADD\s+(?:COLUMN\s+)?\S+\s+TEXT\b", sql, re.IGNORECASE):
        sql = re.sub(r"(\bADD\s+(?:COLUMN\s+)?\S+\s+)TEXT\b", r"\1LONGTEXT", sql, count=1, flags=re.IGNORECASE)
        sql = re.sub(r"(\bDEFAULT\s+)('(?:''|[^'])*')", r"\1(\2)", sql, count=1, flags=re.IGNORECASE)
    sql = re.sub(r"CREATE\s+(UNIQUE\s+)?INDEX\s+IF\s+NOT\s+EXISTS", r"CREATE \1INDEX", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"ON\s+CONFLICT(?:\s*\([^)]*\))?\s+DO\s+UPDATE\s+SET",
        "ON DUPLICATE KEY UPDATE",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(r"\bexcluded\.([A-Za-z_][A-Za-z0-9_]*)", r"VALUES(\1)", sql, flags=re.IGNORECASE)
    return _replace_qmarks(sql)


def _split_sql_list(body: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(body):
        char = body[index]
        if quote:
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(body[start:index].strip())
            start = index + 1
        index += 1
    tail = body[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _constraint_columns(part: str) -> list[str]:
    match = re.search(r"\(([^()]*)\)", part, re.DOTALL)
    if not match:
        return []
    return [item.strip().strip("`").split()[0].strip("`") for item in match.group(1).split(",") if item.strip()]


def _mysqlize_create_table(sql: str) -> str:
    opening = sql.find("(")
    closing = sql.rfind(")")
    if opening < 0 or closing <= opening:
        return sql
    body = sql[opening + 1 : closing]
    parts = _split_sql_list(body)
    key_columns: set[str] = set()
    for part in parts:
        upper = part.lstrip().upper()
        if upper.startswith(("PRIMARY KEY", "UNIQUE", "FOREIGN KEY")):
            key_columns.update(_constraint_columns(part))
        else:
            definition = re.match(r"^`?([A-Za-z0-9_]+)`?\s+(.+)$", part, re.DOTALL)
            if definition and re.search(r"\bPRIMARY\s+KEY\b|\bUNIQUE\b", definition.group(2), re.IGNORECASE):
                key_columns.add(definition.group(1))

    rewritten: list[str] = []
    for part in parts:
        if re.match(r"^(?:PRIMARY\s+KEY|UNIQUE\b|FOREIGN\s+KEY|CHECK\s*\(|CONSTRAINT\b)", part.lstrip(), re.IGNORECASE):
            rewritten.append(part)
            continue

        # Translate only the declared column type. A schema contains a column
        # literally named `text`; global TEXT replacement corrupts it into
        # `LONGTEXT LONGTEXT`.
        definition = re.match(
            r"^(\s*)(`?)([A-Za-z0-9_]+)\2(\s+)(INTEGER|INT|TEXT|REAL|FLOAT|BLOB|NUMERIC)\b(.*)$",
            part,
            re.IGNORECASE | re.DOTALL,
        )
        if definition:
            leading, quote, column, gap, source_type, tail = definition.groups()
            normalized_type = source_type.upper()
            if normalized_type in {"INTEGER", "INT"}:
                if re.match(r"\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", tail, re.IGNORECASE):
                    tail = re.sub(r"^\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", " AUTO_INCREMENT PRIMARY KEY", tail, count=1, flags=re.IGNORECASE)
                    target_type = "BIGINT"
                else:
                    target_type = "BIGINT"
            elif normalized_type == "TEXT":
                target_type = "VARCHAR(191)" if column in key_columns else "LONGTEXT"
                default_match = re.search(r"\bDEFAULT\s+('(?:''|[^'])*')", tail, re.IGNORECASE)
                if target_type == "LONGTEXT" and default_match:
                    literal = default_match.group(1)
                    tail = tail[: default_match.start(1)] + f"({literal})" + tail[default_match.end(1) :]
            elif normalized_type == "REAL":
                target_type = "DOUBLE"
            elif normalized_type == "FLOAT":
                target_type = "FLOAT"
            elif normalized_type == "BLOB":
                target_type = "LONGBLOB"
            else:
                target_type = "DECIMAL(20, 6)"
            part = f"{leading}{quote}{column}{quote}{gap}{target_type}{tail}"
        rewritten.append(part)
    return sql[: opening + 1] + ", ".join(rewritten) + sql[closing:]


def database_name_for_company(company_id: str | None = None) -> str:
    if company_id in {None, "", "company_default", "default"}:
        company_id = "default"
    suffix = re.sub(r"[^a-zA-Z0-9_]", "_", company_id or "default").lower()[:48]
    return f"{settings.mysql_database_prefix}_{suffix}"


def existing_company_databases() -> set[str]:
    """List existing tenant databases without creating missing databases."""
    prefix = f"{settings.mysql_database_prefix}_"
    connection = pymysql.connect(**_connect_kwargs())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA "
                "WHERE LEFT(SCHEMA_NAME, %s) = %s",
                (len(prefix), prefix),
            )
            return {str(row["SCHEMA_NAME"]) for row in cursor.fetchall()}
    finally:
        connection.close()


def _connect_kwargs(database: str | None = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "host": settings.mysql_host,
        "port": settings.mysql_port,
        "user": settings.mysql_user,
        "password": settings.mysql_password,
        "charset": "utf8mb4",
        "autocommit": False,
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": max(1, int(settings.mysql_connect_timeout_seconds)),
        "read_timeout": max(1, int(settings.mysql_read_timeout_seconds)),
        "write_timeout": max(1, int(settings.mysql_write_timeout_seconds)),
    }
    if database:
        kwargs["database"] = database
    return kwargs


def ensure_database(database: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise ValueError(f"Invalid MySQL database name: {database}")
    connection = pymysql.connect(**_connect_kwargs())
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s", (database,))
            if cursor.fetchone() is None:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        connection.commit()
    finally:
        connection.close()


class MySQLConnection:
    def __init__(self, database: str):
        ensure_database(database)
        self._connection = pymysql.connect(**_connect_kwargs(database))
        self.row_factory: Any = None

    def execute(self, sql: str, params: Iterable[Any] | None = None) -> CompatCursor:
        translated = _translate_sql(sql)
        try:
            cursor = self._connection.cursor()
            cursor.execute(translated, tuple(params or ()))
            return CompatCursor(cursor)
        except pymysql.err.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        except pymysql.err.OperationalError as exc:
            # SQLite's CREATE INDEX IF NOT EXISTS is used throughout the
            # codebase. MySQL has no equivalent; a duplicate index is safe.
            if exc.args and exc.args[0] == 1061 and translated.lstrip().upper().startswith("CREATE"):
                return CompatCursor(_empty_cursor())
            if exc.args and exc.args[0] == 1170 and translated.lstrip().upper().startswith("CREATE"):
                return self._retry_index_with_prefix(translated)
            raise sqlite3.OperationalError(str(exc)) from exc
        except pymysql.MySQLError as exc:
            raise sqlite3.OperationalError(str(exc)) from exc

    def executemany(self, sql: str, seq_of_params: Iterable[Iterable[Any]]) -> CompatCursor:
        translated = _translate_sql(sql)
        try:
            cursor = self._connection.cursor()
            cursor.executemany(translated, [tuple(params) for params in seq_of_params])
            return CompatCursor(cursor)
        except pymysql.err.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        except pymysql.MySQLError as exc:
            raise sqlite3.OperationalError(str(exc)) from exc

    def _retry_index_with_prefix(self, translated: str) -> CompatCursor:
        match = re.search(r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+`?([\w]+)`?\s+ON\s+`?([\w]+)`?\s*\(([^)]+)\)", translated, re.I)
        if not match:
            raise sqlite3.OperationalError("MySQL index creation failed")
        index_name, table_name, columns = match.groups()
        text_columns: set[str] = set()
        cursor = self._connection.cursor()
        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        for row in cursor.fetchall():
            if str(row.get("Type", "")).lower().startswith(("text", "mediumtext", "longtext", "tinytext", "blob", "mediumblob", "longblob", "tinyblob", "varchar", "char")):
                text_columns.add(str(row.get("Field")))
        rewritten: list[str] = []
        for column in columns.split(","):
            token = column.strip()
            plain = token.strip("`").split()[0].strip("`")
            rewritten.append(f"`{plain}`(191)" if plain in text_columns else token)
        cursor = self._connection.cursor()
        try:
            cursor.execute(f"CREATE INDEX `{index_name}` ON `{table_name}` ({', '.join(rewritten)})")
        except pymysql.err.IntegrityError as exc:
            raise sqlite3.IntegrityError(str(exc)) from exc
        return CompatCursor(cursor)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "MySQLConnection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()


def _empty_cursor():
    return _NoopCursor()


class _NoopCursor:
    rowcount = 0
    lastrowid = 0
    description: list[Any] = []

    def fetchone(self):
        return None

    def fetchall(self):
        return []
