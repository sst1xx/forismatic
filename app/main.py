import asyncio
import sys
import logging
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

from .database import init_db, get_random_quote, count_quotes
from .fetcher import TARGET

log = logging.getLogger(__name__)


async def _start_fetcher():
    """Запускаем fetcher с небольшой задержкой после старта сервера."""
    await asyncio.sleep(2)
    log.info("Запускаем fetcher как фоновый процесс...")
    subprocess.Popen(
        [sys.executable, "-m", "app.fetcher"],
        start_new_session=True,
        close_fds=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    count = await count_quotes()
    log.info(f"База содержит {count} записей (цель: {TARGET}).")
    # Откладываем запуск fetcher на 2 секунды после старта сервера.
    # asyncio.create_task не создаёт тредов — Popen вызовется уже после
    # того как uvicorn полностью запустился и lifespan завершён.
    asyncio.create_task(_start_fetcher())
    yield


app = FastAPI(
    title="Forismatic",
    docs_url=None,
    redoc_url=None,
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
