# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# В dev-сборке (INSTALL_DEV=1) ставим pytest; в продакшен-образ он не попадает
ARG INSTALL_DEV=0
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_DEV" = "1" ]; then pip install -r requirements-dev.txt; fi

COPY . .

# Загрузки (фото лотов) — отдельный том; каталог принадлежит пользователю app
RUN mkdir -p /app/uploads && chown app:app /app/uploads

USER app

EXPOSE 8000

CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "wsgi:app"]
