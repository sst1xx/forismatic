import os
import aiosqlite

DB_PATH = os.environ.get("DB_PATH", "/data/quotes.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
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
        async with db.execute(
            "SELECT text, author FROM quotes ORDER BY RANDOM() LIMIT 1"
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            text, author = row
            # Убедимся что текст оканчивается точкой/знаком
            text = text.strip()
            if author:
                author = author.strip()
                return f"{text} {author}"
            return text


async def insert_quotes(quotes: list[tuple[str, str | None]]):
    """
    quotes: list of (text, author)
    Дубликаты по тексту игнорируются.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO quotes (text, author) VALUES (?, ?)",
            quotes,
        )
        await db.commit()
