import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

from .database import init_db, get_random_quote, count_quotes
from .fetcher import run_fetch, TARGET

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    count = await count_quotes()
    # За один рестарт загружаем не более 3000 новых записей.
    # При каждом следующем рестарте порог сдвигается, пока не достигнет TARGET.
    batch_target = min(count + 3000, TARGET)
    log.info(f"База содержит {count} записей. Цель этого запуска: {batch_target} (макс: {TARGET}).")
    asyncio.create_task(run_fetch(target=batch_target))
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
