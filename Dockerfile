FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Директория для SQLite volume (создаётся если не смонтирован)
RUN mkdir -p /data

EXPOSE 8000

# uvicorn является PID 1 — корректно получает SIGTERM от Docker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
