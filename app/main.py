import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

from .database import init_db, get_random_quote, count_quotes
from .fetcher import run_fetch

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация БД
    await init_db()
    # Если база пустая - запустить загрузку в фоне
    count = await count_quotes()
    if count == 0:
        log.info("База пустая, запускаем загрузку цитат в фоне...")
        asyncio.create_task(run_fetch(target=5000))
    else:
        log.info(f"База содержит {count} цитат.")
    yield


app = FastAPI(
    title="Forismatic",
    docs_url=None,   # Отключаем swagger UI
    redoc_url=None,  # Отключаем redoc
    openapi_url=None,
    lifespan=lifespan,
)


@app.get("/", status_code=200)
async def root():
    return Response(content="", media_type="text/plain")


@app.get("/quote", response_class=PlainTextResponse)
async def quote():
    q = await get_random_quote()
    if q is None:
        return PlainTextResponse("База цитат пуста. Повторите запрос позже.", status_code=503)
    return PlainTextResponse(q, media_type="text/plain; charset=utf-8")
