import os
import sqlite3
import asyncio
from functools import partial
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "/data/quotes.db")

_INSERT_BATCH = 200


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db_sync():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            author TEXT
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_text ON quotes(text)")
    conn.commit()
    conn.close()


def _count_quotes_sync() -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()
    conn.close()
    return row[0] if row else 0


def _get_random_quote_sync() -> Optional[str]:
    conn = _connect()
    row = conn.execute("""
        SELECT text, author FROM quotes
        WHERE id >= (ABS(RANDOM()) % (SELECT MAX(id) FROM quotes) + 1)
        ORDER BY id LIMIT 1
    """).fetchone()
    # Fallback: random попал выше max реального id
    if row is None:
        row = conn.execute(
            "SELECT text, author FROM quotes ORDER BY id LIMIT 1"
        ).fetchone()
    conn.close()
    if row is None:
        return None
    text, author = row
    text = text.strip()
    if author:
        return f"{text} {author.strip()}"
    return text


def _insert_quotes_sync(quotes: list):
    if not quotes:
        return
    conn = _connect()
    for i in range(0, len(quotes), _INSERT_BATCH):
        batch = quotes[i : i + _INSERT_BATCH]
        conn.executemany(
            "INSERT OR IGNORE INTO quotes (text, author) VALUES (?, ?)",
            batch,
        )
        conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Async-обёртки: выполняют sync-функции в thread pool executor.
# В отличие от aiosqlite не создают постоянный тред на соединение —
# executor берёт тред из пула и возвращает его после завершения.
# ---------------------------------------------------------------------------

async def init_db():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _init_db_sync)


async def count_quotes() -> int:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _count_quotes_sync)


async def get_random_quote() -> Optional[str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _get_random_quote_sync)


async def insert_quotes(quotes: list):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, partial(_insert_quotes_sync, quotes))
