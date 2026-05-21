FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Директория для SQLite volume (создаётся если не смонтирован)
RUN mkdir -p /data

EXPOSE 8000

# uvicorn является PID 1 — корректно получает SIGTERM от Docker
# --loop asyncio: явно отключаем uvloop (тянется транзитивно через fastapi->uvicorn[standard])
# uvloop требует тредов при старте/завершении — crash на серверах с низким ulimit -u
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--loop", "asyncio"]
