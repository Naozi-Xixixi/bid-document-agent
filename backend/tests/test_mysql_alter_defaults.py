from app.services.mysql_compat import _translate_sql


def test_alter_text_default_uses_mysql_expression():
    sql = _translate_sql("ALTER TABLE bid_catalog_chapters ADD COLUMN ref_chart_ids TEXT NOT NULL DEFAULT '[]'")
    assert "ref_chart_ids LONGTEXT NOT NULL DEFAULT ('[]')" in sql


def test_alter_non_text_default_is_unchanged():
    sql = _translate_sql('ALTER TABLE example ADD COLUMN count INTEGER NOT NULL DEFAULT 0')
    assert 'INTEGER NOT NULL DEFAULT 0' in sql


def test_existing_database_is_not_created_again(monkeypatch):
    from app.services import mysql_compat
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): calls.append((sql, params))
        def fetchone(self): return {'SCHEMA_NAME': 'wordagent_existing'}
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(mysql_compat.pymysql, 'connect', lambda **kwargs: Connection())
    mysql_compat.ensure_database('wordagent_existing')
    assert len(calls) == 1 and 'INFORMATION_SCHEMA.SCHEMATA' in calls[0][0]


def test_missing_database_is_created(monkeypatch):
    from app.services import mysql_compat
    calls = []
    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, sql, params=None): calls.append((sql, params))
        def fetchone(self): return None
    class Connection:
        def cursor(self): return Cursor()
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(mysql_compat.pymysql, 'connect', lambda **kwargs: Connection())
    mysql_compat.ensure_database('wordagent_new')
    assert len(calls) == 2 and 'CREATE DATABASE IF NOT EXISTS' in calls[1][0]
