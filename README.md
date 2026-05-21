# forismatic

Минималистичный API сервис — возвращает случайную русскую цитату или интересный факт одной строкой. Оптимизирован для ESP32 и других embedded клиентов.

## Быстрый старт

```bash
# Скачать docker-compose.yml
curl -O https://raw.githubusercontent.com/sst1xx/forismatic/main/docker-compose.yml

# Запустить
docker compose up -d
```

Сервис доступен на `http://localhost:8000`.

## API

| Запрос | Ответ |
|---|---|
| `GET /` | `200 OK`, пустое тело |
| `GET /quote` | `200 OK`, одна строка текста |

```bash
curl http://localhost:8000/quote
# Краткость - сестра таланта. Антон Чехов
```

Формат ответа: `text/plain; charset=utf-8`. Если автор известен — добавляется через пробел после текста.

## База цитат

При первом старте загружается **3 000 записей**, при каждом следующем рестарте — ещё 3 000, пока не накопится **20 000**. После этого при каждом рестарте подтягиваются только свежие факты из Wikipedia «Знаете ли вы».

Источники: WikiQuote RU, Wikipedia «Знаете ли вы», aphorism.ru, citaty.info.

Следить за загрузкой:

```bash
docker compose logs -f
```

## Обновление

```bash
docker compose pull && docker compose up -d
```

## Ресурсы

- Память: лимит **256 MB**
- CPU: лимит **1 ядро**
- Хранилище: SQLite в `./data/quotes.db` (Docker volume)

## Docker Hub

```
vitalplay/forismatic:latest
vitalplay/forismatic:YYYY.MM.DD
```

Образ собран для `linux/amd64` и `linux/arm64`.

## Совместимость

Работает на любом Linux-сервере включая машины с ограниченным количеством тредов (`ulimit -u`). Для этого uvicorn запускается с `--loop asyncio` (без uvloop).
