# AGENTS.md

## Project

Minimal FastAPI service returning random Russian quotes/facts as `text/plain` — designed for ESP32 clients. No JSON, no frontend.

## Stack

- Python 3.12, FastAPI, **stdlib sqlite3** (no aiosqlite), **stdlib urllib.request** (no httpx), BeautifulSoup4 (`html.parser` — no lxml)
- SQLite at `/data/quotes.db` (Docker volume `./data`)
- `DB_PATH` env var overrides the path (useful for tests: `DB_PATH=/tmp/test.db`)

## Run

### Сборка и публикация на Docker Hub (локально, на машине разработчика)

```bash
# Один раз: создать multi-platform builder
docker buildx create --name multiarch --driver docker-container --use --bootstrap

# Сборка и пуш сразу для amd64 + arm64 (--push пишет напрямую в Docker Hub)
VERSION=$(date +%Y.%m.%d)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -t vitalplay/forismatic:$VERSION \
  -t vitalplay/forismatic:latest \
  --push \
  .
```

Теги: `vitalplay/forismatic:YYYY.MM.DD` + `vitalplay/forismatic:latest`.
Пользователь Docker Hub: **vitalplay** (уже залогинен).
Образ локально после `buildx --push` не появляется — это нормально, он сразу в registry.

### Деплой на сервере (только docker-compose.yml — без кода)

```bash
docker compose pull            # скачать свежий образ с Docker Hub
docker compose up -d           # запустить / перезапустить
docker compose logs -f         # наблюдать за fetcher
docker compose restart         # re-triggers fetcher (adds up to 3000 new records)
```

CRITICAL: на сервере нет `build:` секции — образ берётся с Docker Hub.
После любых правок в `app/` или `requirements.txt`:
1. Собери и запуши новый образ (команды выше)
2. На сервере: `docker compose pull && docker compose up -d`

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

Each restart loads at most **3 000 new records** (`batch_target = min(count + 3000, TARGET)`), defined in `app/fetcher.py`. The DB grows across restarts until it hits TARGET.

Once TARGET is reached, the DB is considered complete — new restarts just load BUILTIN_QUOTES (idempotent, no HTTP). `INSERT OR IGNORE` silently drops duplicates — dedup is by exact `text` value (UNIQUE index).

## Data sources (order matters in `run_fetch`)

1. **BUILTIN_QUOTES** — ~100 hardcoded classics, idempotent
2. **WikiQuote RU** (`ru.wikiquote.org/w/api.php`) — quotes by author pages from `WIKIAUTHORS` list
3. **aphorism.ru** — `/today/` + `/archive/YYYY/M/D/` (up to 2 years back); encoding cp1251; selectors: `a[href*="/comments/"]` for text, `a[href*="/author/"]` for author
4. **citaty.info** — `/man?page=N` listing (0-indexed, up to 150 pages); encoding UTF-8; selectors: `a[href*="/quote/"]` for text, `a[title="Автор цитаты"]` for author

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

aphorism.ru uses Windows-1251 encoding — always decode with `cp1251`, not `utf-8`.

citaty.info changed its URL structure: old paths `/citaty/`, `/aforizmy/` etc. return 404. Use `/man?page=N`.

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

`get_random_quote` uses `ORDER BY RANDOM() LIMIT 1` — uniform distribution over all rows. The previous `WHERE id >= random % MAX(id)` approach over-sampled quotes with large id gaps before them.

## Known gotchas

### `RuntimeError: can't start new thread` on low-resource servers

Root cause: server has a very strict `ulimit -u` (process/thread limit). Any library
that creates threads internally will crash — even one thread is too many.

Three layers of fixes were applied:

**1. uvloop** — `fastapi==0.111.0` pulls `uvicorn[standard]` → `uvloop`. On Linux,
uvicorn auto-selects uvloop, which creates threads on startup and shutdown.
Fix: `--loop asyncio` in Dockerfile CMD forces stdlib event loop:
```dockerfile
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
```

**2. aiosqlite** — creates a dedicated thread per connection.
Fix: replaced with plain `sqlite3` called directly in async functions (no threads, no `run_in_executor`).

**3. httpx** — `AsyncClient` creates an internal thread pool for connection pooling and DNS.
Fix: replaced with stdlib `urllib.request` — synchronous, zero threads. Works fine
because fetcher runs as a separate OS process (not inside uvicorn's event loop).

Mac is unaffected because uvloop has no wheel for `darwin/arm64`, and httpx on Mac
does not hit thread limits in development.

### fetcher.py is fully synchronous

fetcher runs as a separate process (`subprocess.Popen` from `main.py` lifespan).
It uses `urllib.request`, plain `sqlite3`, and `time.sleep` — no asyncio, no threads.
Do NOT reintroduce httpx, aiosqlite, or asyncio into fetcher.py.

### database.py uses no threads

`sqlite3` is called directly in async functions without `run_in_executor`.
SQLite ops take microseconds at this workload — no event loop blocking in practice.
Do not reintroduce aiosqlite or run_in_executor.

## Testing without Docker

```bash
DB_PATH=/tmp/test.db uvicorn app.main:app --reload --loop asyncio
```

Quick syntax check:
```bash
python3 -c "import ast; ast.parse(open('app/fetcher.py').read())"
```
