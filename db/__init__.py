"""
db/__init__.py — Auto-selects SQLite or PostgreSQL backend at *call time*.

If DATABASE_URL is present in the environment → PostgreSQL (db/pg_database.py).
Otherwise                                      → SQLite   (db/database.py).

The selection is deferred to call time (not import time) so that load_dotenv()
in app.py runs before the first database call — otherwise DATABASE_URL would
not yet be visible when this module is first imported.

Python's import cache means the inner `from .xxx import` only resolves once per
process, so there is no per-call overhead after the first invocation.
"""
from __future__ import annotations
import os
from contextlib import contextmanager


def get_db():
    """Return a live database connection (auto-selects SQLite or PostgreSQL)."""
    if os.environ.get("DATABASE_URL"):
        from .pg_database import get_db as _f
    else:
        from .database import get_db as _f
    return _f()


@contextmanager
def db_session():
    """
    Context manager: yields a connection, auto-commits on success,
    rolls back on exception, then closes.
    """
    if os.environ.get("DATABASE_URL"):
        from .pg_database import db_session as _cm
    else:
        from .database import db_session as _cm
    with _cm() as conn:
        yield conn


def init_db():
    """
    Initialise / migrate the database schema.
    Called once at Flask startup (create_app → init_db()).
    """
    if os.environ.get("DATABASE_URL"):
        from .pg_database import init_db as _f
    else:
        from .database import init_db as _f
    return _f()


__all__ = ["get_db", "db_session", "init_db"]
