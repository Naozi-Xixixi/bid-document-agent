from __future__ import annotations

"""Migrate the existing per-company SQLite databases to MySQL.

Run this while the backend is stopped.  The target databases are created as
``MYSQL_AUTH_DATABASE`` and ``MYSQL_DATABASE_PREFIX_<company>``.  Records are
inserted with ``INSERT IGNORE`` so the command can be resumed safely.
"""

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.services.mysql_compat import MySQLConnection, _translate_sql, database_name_for_company, ensure_database


def _ident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(f"Unsafe SQL identifier: {value}")
    return f"`{value}`"


def _source_tables(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def _source_indexes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _destination_columns(conn: MySQLConnection, table: str) -> list[str]:
    return [str(row["Field"]) for row in conn.execute(f"SHOW COLUMNS FROM {_ident(table)}").fetchall()]


def _create_tables(source: sqlite3.Connection, target: MySQLConnection, tables: list[tuple[str, str]]) -> None:
    target.execute("SET FOREIGN_KEY_CHECKS = 0")
    pending = list(tables)
    last_error: Exception | None = None
    while pending:
        next_pending: list[tuple[str, str]] = []
        for table, source_sql in pending:
            sql = re.sub(r"^\s*CREATE\s+TABLE\s+", "CREATE TABLE IF NOT EXISTS ", source_sql, count=1, flags=re.I)
            try:
                target.execute(sql)
            except Exception as exc:
                last_error = exc
                next_pending.append((table, source_sql))
        if len(next_pending) == len(pending):
            raise RuntimeError(f"无法创建 MySQL 表，最近错误: {last_error}")
        pending = next_pending
    target.execute("SET FOREIGN_KEY_CHECKS = 1")


def _create_indexes(target: MySQLConnection, indexes: list[str]) -> None:
    for source_sql in indexes:
        sql = re.sub(r"^\s*CREATE\s+", "CREATE ", source_sql, count=1, flags=re.I)
        sql = re.sub(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+", r"CREATE \1INDEX IF NOT EXISTS ", sql, count=1, flags=re.I)
        try:
            target.execute(sql)
        except Exception as exc:
            # Indexes over TEXT columns may need a 191-character prefix. The
            # adapter retries those automatically; report only unrecoverable
            # failures so data migration can continue when an optional index
            # is incompatible with a server collation.
            print(f"[warn] 跳过索引: {source_sql} ({exc})")


def _copy_rows(source: sqlite3.Connection, target: MySQLConnection, table: str, batch_size: int = 500) -> int:
    source_columns = [str(row[1]) for row in source.execute(f"PRAGMA table_info({_ident(table).strip('`')})").fetchall()]
    target_columns = _destination_columns(target, table)
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        return 0
    column_sql = ", ".join(_ident(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = f"INSERT IGNORE INTO {_ident(table)} ({column_sql}) VALUES ({placeholders})"
    cursor = source.execute(f"SELECT {column_sql} FROM {_ident(table).strip('`')}")
    count = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        target.executemany(insert_sql, [tuple(row[column] for column in columns) for row in rows])
        target.commit()
        count += len(rows)
    return count


def migrate_database(source_path: Path, database: str) -> dict[str, Any]:
    if not source_path.exists():
        return {"source": str(source_path), "database": database, "status": "missing", "tables": 0, "rows": 0}
    print(f"[start] {source_path} -> {database}")
    ensure_database(database)
    with sqlite3.connect(source_path) as source, MySQLConnection(database) as target:
        source.row_factory = sqlite3.Row
        tables = _source_tables(source)
        _create_tables(source, target, tables)
        target.commit()
        _create_indexes(target, _source_indexes(source))
        target.commit()
        total_rows = 0
        table_stats: dict[str, int] = {}
        for table, _ in tables:
            copied = _copy_rows(source, target, table)
            table_stats[table] = copied
            total_rows += copied
            if copied:
                print(f"  {table}: {copied} 行")
        target.commit()
    return {"source": str(source_path), "database": database, "status": "completed", "tables": len(tables), "rows": total_rows, "table_stats": table_stats}


def _auth_companies(auth_path: Path) -> list[tuple[str, Path]]:
    if not auth_path.exists():
        return []
    with sqlite3.connect(auth_path) as conn:
        rows = conn.execute("SELECT company_id, database_path FROM auth_company ORDER BY company_id").fetchall()
    return [(str(row[0]), Path(str(row[1]))) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate WordAgent SQLite databases to MySQL")
    parser.add_argument("--source-data", type=Path, default=settings.resolve_local_path(settings.data_dir))
    parser.add_argument("--company-id", help="only migrate one company")
    parser.add_argument("--skip-auth", action="store_true")
    args = parser.parse_args()

    if (settings.db_backend or "sqlite").lower() != "mysql":
        raise SystemExit("请先在 backend/.env 设置 DB_BACKEND=mysql，并填写 MYSQL_* 连接信息")
    source_data = args.source_data.resolve()
    auth_source = source_data / "auth.sqlite3"
    results: list[dict[str, Any]] = []

    companies = _auth_companies(auth_source)
    if not args.skip_auth:
        results.append(migrate_database(auth_source, settings.mysql_auth_database))
    for company_id, database_path in companies:
        if args.company_id and company_id != args.company_id:
            continue
        destination = database_name_for_company(company_id)
        source_path = database_path if database_path.is_absolute() else (source_data / database_path)
        # The auth record contains the absolute path in normal installations;
        # this fallback also handles old relative records.
        results.append(migrate_database(source_path.resolve(), destination))

    if not companies and not args.company_id:
        results.append(migrate_database(source_data / "wordagent.sqlite3", database_name_for_company("default")))

    print("\n迁移完成:")
    for result in results:
        print(f"- {result['database']}: {result['status']}, {result['rows']} 行, {result['tables']} 张表")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    raise SystemExit(main())
