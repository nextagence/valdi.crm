"""Подключение к SQLite."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g


def _lower(value):
    return value.lower() if isinstance(value, str) else value


def _digits(value):
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value if ch.isdigit())


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Регистронезависимый поиск по кириллице: встроенный LOWER() в SQLite — только ASCII
    conn.create_function("py_lower", 1, _lower, deterministic=True)
    conn.create_function("digits", 1, _digits, deterministic=True)
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_schema(conn: sqlite3.Connection):
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(schema)
    conn.commit()


def query(sql: str, args=(), one: bool = False):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return (rows[0] if rows else None) if one else rows


def scalar(sql: str, args=()):
    row = get_db().execute(sql, args).fetchone()
    return row[0] if row else None
