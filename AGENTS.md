# AGENTS.md

## Project

Minimal FastAPI service returning random Russian quotes/facts as `text/plain` — designed for ESP32 clients. No JSON, no frontend.

## Stack

- Python 3.12, FastAPI, aiosqlite, httpx, BeautifulSoup4/lxml
- SQLite at `/data/quotes.db` (Docker volume `./data`)
- `DB_PATH` env var overrides the path (useful for tests: `DB_PATH=/tmp/test.db`)

## Run

```bash
docker compose up -d          # start (fetcher runs in background on every start)
docker compose logs -f        # watch fetcher progress
docker compose restart        # re-triggers fetcher (adds up to 3000 new records)
```

Manual fetcher run (e.g. to force full reload):
```bash
docker compose exec app python -m app.fetcher
```

## API

| Endpoint | Response |
|---|---|
| `GET /` | `200 OK`, empty body |
| `GET /quote` | `200 OK`, `text/plain; charset=utf-8`, one line |

Quote format: `Текст цитаты. Автор` — no em-dashes, author appended after text with a space. If no author: just the text.

## DB fill strategy

`TARGET = 20_000` in `app/fetcher.py`.

Each restart loads at most **3 000 new records** (`batch_target = min(count + 3000, TARGET)`), defined in `app/main.py`. The DB grows across restarts until it hits TARGET.

Once TARGET is reached, every restart only fetches the current `Шаблон:Знаете_ли_вы` template (~11 facts, 1 HTTP request) to keep content fresh. `INSERT OR IGNORE` silently drops duplicates — dedup is by exact `text` value (UNIQUE index).

## Data sources (order matters in `run_fetch`)

1. **Current ЗЛВ template** — always fetched first on every start (`fetch_did_you_know_current`)
2. **BUILTIN_QUOTES** — ~100 hardcoded classics, idempotent
3. **WikiQuote RU** (`ru.wikiquote.org/w/api.php`) — quotes by author pages from `WIKIAUTHORS` list
4. **Wikipedia ЗЛВ archive** (`ru.wikipedia.org/w/api.php`, `Проект:Знаете_ли_вы/Архив_рубрики/YYYY-MM`) — curated facts, archive from 2008
5. **aphorism.ru** — scraping, may be slow or blocked
6. **citaty.info** — scraping, may be slow or blocked

## Parsing quirks

`is_valid()` rejects:
- text shorter than 20 chars or longer than 500
- no Cyrillic characters
- starts with `:`, `категория:`, `см.`, `wikiquote`
- fewer than 4 words (catches section headers like "Критика и публицистика")

`clean_text()` normalises:
- em-dash / en-dash → ` - `
- strips space before punctuation (artifact of removed `{{шаблон}}` in wikitext)

WikiQuote parser skips lines containing `[http` before any processing — those are "Ссылки" section entries, not quotes.

## User-Agent

All HTTP clients use:
```
forismatic-bot/1.0 (https://github.com/forismatic; educational project)
```
Wikipedia returns `403` with a browser UA — do not change this.

## Schema

```sql
quotes(id INTEGER PK, text TEXT NOT NULL, author TEXT)
-- UNIQUE INDEX on text — dedup key
```

No migrations. Schema created via `CREATE TABLE IF NOT EXISTS` on every startup.

## Testing without Docker

```bash
DB_PATH=/tmp/test.db uvicorn app.main:app --reload   # requires venv with requirements.txt
```

Quick syntax check:
```bash
python3 -c "import ast; ast.parse(open('app/fetcher.py').read())"
```
