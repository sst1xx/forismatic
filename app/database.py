import os
import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "/data/quotes.db")

# Размер батча для INSERT — короче блокировка, меньше пиковая память транзакции
_INSERT_BATCH = 200


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL mode: reader (uvicorn) и writer (fetcher) не блокируют друг друга
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                author TEXT
            )
        """)
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_quotes_text ON quotes(text)")
        await db.commit()


async def count_quotes() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM quotes") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_random_quote() -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        # O(1) вместо O(n) ORDER BY RANDOM():
        # выбираем id >= случайного значения в диапазоне [1, max_id], берём первый.
        # ORDER BY id гарантирует детерминированный результат при пропусках в id.
        async with db.execute(
            """
            SELECT text, author FROM quotes
            WHERE id >= (ABS(RANDOM()) % (SELECT MAX(id) FROM quotes) + 1)
            ORDER BY id LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
            # Fallback: если random попал в "хвост" выше max реального id (маловероятно)
            if row is None:
                async with db.execute(
                    "SELECT text, author FROM quotes ORDER BY id LIMIT 1"
                ) as cur2:
                    row = await cur2.fetchone()
            if row is None:
                return None
            text, author = row
            text = text.strip()
            if author:
                return f"{text} {author.strip()}"
            return text


async def insert_quotes(quotes: list[tuple[str, str | None]]):
    """
    Вставляет цитаты батчами по _INSERT_BATCH штук.
    Дубликаты по тексту игнорируются (UNIQUE index + INSERT OR IGNORE).
    """
    if not quotes:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        for i in range(0, len(quotes), _INSERT_BATCH):
            batch = quotes[i : i + _INSERT_BATCH]
            await db.executemany(
                "INSERT OR IGNORE INTO quotes (text, author) VALUES (?, ?)",
                batch,
            )
            await db.commit()
