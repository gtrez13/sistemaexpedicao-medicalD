"""
db/pg_database.py — PostgreSQL database layer with sqlite3-compatible interface.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL", DATABASE_URL)
        if not url:
            raise RuntimeError("DATABASE_URL não está definido.")
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, url)
    return _pool


_RE_QMARK     = re.compile(r'\?')
_RE_NOCASE    = re.compile(r'\s+COLLATE\s+NOCASE\b', re.IGNORECASE)
_RE_BEGIN_IMM = re.compile(r'\bBEGIN\s+IMMEDIATE\b', re.IGNORECASE)
_RE_AUTOINCR  = re.compile(r'\bAUTOINCREMENT\b', re.IGNORECASE)


def _adapt(sql: str) -> str:
    sql = _RE_QMARK.sub('%s', sql)
    sql = _RE_NOCASE.sub('', sql)
    sql = _RE_BEGIN_IMM.sub('BEGIN', sql)       # SQLite → PostgreSQL
    sql = _RE_AUTOINCR.sub('', sql)             # AUTOINCREMENT não existe no PG
    return sql


class _PgRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


def _wrap_rows(rows) -> list[_PgRow]:
    return [_PgRow(r) for r in rows] if rows else []


class _ResultProxy:
    __slots__ = ("_cur",)

    def __init__(self, cur) -> None:
        self._cur = cur

    def fetchall(self) -> list[_PgRow]:
        return _wrap_rows(self._cur.fetchall())

    def fetchone(self) -> _PgRow | None:
        r = self._cur.fetchone()
        return _PgRow(r) if r else None

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount


class PgCursor:
    __slots__ = ("_c",)

    def __init__(self, raw) -> None:
        self._c = raw

    def execute(self, sql: str, params=()) -> "PgCursor":
        self._c.execute(_adapt(sql), params)
        return self

    def fetchall(self) -> list[_PgRow]:
        return _wrap_rows(self._c.fetchall())

    def fetchone(self) -> _PgRow | None:
        r = self._c.fetchone()
        return _PgRow(r) if r else None

    @property
    def rowcount(self) -> int:
        return self._c.rowcount


class PgConn:
    def __init__(self, raw_conn) -> None:
        self._conn = raw_conn
        self._cur  = raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params=()) -> _ResultProxy:
        self._cur.execute(_adapt(sql), params)
        return _ResultProxy(self._cur)

    def cursor(self) -> PgCursor:
        c = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        return PgCursor(c)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        try:
            _get_pool().putconn(self._conn)
        except Exception:
            try:
                self._conn.close()
            except Exception:
                pass


def get_db() -> PgConn:
    conn = _get_pool().getconn()
    conn.autocommit = False
    return PgConn(conn)


@contextmanager
def db_session():
    pg = get_db()
    try:
        yield pg
        pg.commit()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()


def _split_statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    in_dq = False
    i = 0
    n = len(sql)

    while i < n:
        if sql[i : i + 2] == "$$":
            in_dq = not in_dq
            buf.append("$$")
            i += 2
            continue
        if sql[i] == ";" and not in_dq:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt + ";")
            buf = []
            i += 1
            continue
        buf.append(sql[i])
        i += 1

    stmt = "".join(buf).strip()
    if stmt:
        statements.append(stmt)

    return statements


def init_db() -> None:
    """
    Aplica todas as migrations em migrations/0*.sql em ordem.
    Rastreia o que já foi aplicado em schema_migrations para ser idempotente.
    """
    import pathlib
    migrations_dir = pathlib.Path(__file__).parent.parent / "migrations"

    url = os.environ.get("DATABASE_URL", DATABASE_URL)
    raw_conn = psycopg2.connect(url)
    try:
        raw_conn.autocommit = True
        cur = raw_conn.cursor()

        # Garante que a tabela de controle existe antes de tudo
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id         SERIAL      PRIMARY KEY,
                filename   TEXT        UNIQUE NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        migration_files = sorted(migrations_dir.glob("0*.sql"))
        if not migration_files:
            raise FileNotFoundError(f"Nenhuma migration em {migrations_dir}")

        for migration_path in migration_files:
            fname = migration_path.name

            cur.execute("SELECT id FROM schema_migrations WHERE filename = %s", (fname,))
            if cur.fetchone():
                continue  # Já aplicada

            sql = migration_path.read_text(encoding="utf-8")
            cur.execute(sql)

            cur.execute(
                "INSERT INTO schema_migrations (filename) VALUES (%s)", (fname,)
            )
            print(f"✅ Migration aplicada: {fname}", flush=True)

        cur.close()
    finally:
        raw_conn.close()