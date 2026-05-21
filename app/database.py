import os
import sqlite3
from typing import Optional

DB_PATH = os.environ.get("DB_PATH", "/data/quotes.db")

_INSERT_BATCH = 200


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Все функции синхронные — вызываются напрямую из async-кода без executor.
# SQLite-операции занимают микросекунды и не блокируют event loop заметно.
# Это единственный вариант на серверах с жёстким лимитом тредов (ulimit -u).
# ---------------------------------------------------------------------------

async def init_db():
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


async def count_quotes() -> int:
    conn = _connect()
    row = conn.execute("SELECT COUNT(*) FROM quotes").fetchone()
    conn.close()
    return row[0] if row else 0


async def get_random_quote() -> Optional[str]:
    conn = _connect()
    row = conn.execute(
        "SELECT text, author FROM quotes ORDER BY RANDOM() LIMIT 1"
    ).fetchone()
    conn.close()
    if row is None:
        return None
    text, author = row
    text = text.strip()
    if author:
        return f"{text} {author.strip()}"
    return text


async def insert_quotes(quotes: list):
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
