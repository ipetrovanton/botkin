# Dockerfile для Botkin API + Telegram-бот.
# Многоэтапная сборка: установка зависимостей → runtime-образ без dev-инструментов.

FROM python:3.12-slim AS base

WORKDIR /app

# Системные зависимости: libsqlite3 (для sqlite-vec), libjpeg (PIL), libheif.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libsqlite3-0 libjpeg62-turbo libheif1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Установка uv (быстрый пакетный менеджер).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Копируем манифесты зависимостей для кэширования слоя.
COPY pyproject.toml uv.lock ./

# Установка зависимостей проекта.
RUN uv sync --frozen --no-dev --no-install-project

# Копируем исходный код.
COPY src/ ./src/
COPY config.json ./
COPY data/ ./data/

# Точка входа — выбирается через docker-compose command.
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

# По умолчанию запускаем API.
CMD ["uv", "run", "uvicorn", "botkin.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
