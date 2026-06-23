# Vault Zeta — Railway container.
# Slim FastAPI image. No faster-whisper, no sounddevice — STT runs on Groq.

FROM python:3.12-slim AS build

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY core ./core
COPY apps ./apps
COPY infra ./infra

RUN pip install --upgrade pip \
 && pip install --prefix=/install -e .

# ── runtime ───────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Bring in resolved deps + project metadata
COPY --from=build /install /usr/local
COPY pyproject.toml ./
COPY core ./core
COPY apps/api ./apps/api
COPY apps/__init__.py ./apps/__init__.py
COPY infra/migrations ./infra/migrations

# Re-install in editable mode to register the project package paths.
RUN pip install -e .

# Railway injects $PORT. Default to 8000 for local container runs.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn apps.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers"]
