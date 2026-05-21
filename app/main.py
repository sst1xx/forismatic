import sys
import logging
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse

from .database import init_db, get_random_quote, count_quotes
from .fetcher import TARGET

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    count = await count_quotes()
    log.info(f"База содержит {count} записей (цель: {TARGET}).")
    # Запускаем fetcher как отдельный OS-процесс в новой сессии.
    # - start_new_session=True: процесс выходит из группы uvicorn,
    #   SIGTERM от Docker до него не доходит — fetcher доживает до конца.
    # - close_fds=True: не наследует файловые дескрипторы uvicorn.
    # - Popen не блокирует и не оставляет зомби: мы не вызываем wait(),
    #   процесс будет усыновлён init (PID 1 внутри контейнера).
    subprocess.Popen(
        [sys.executable, "-m", "app.fetcher"],
        start_new_session=True,
        close_fds=True,
    )
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
