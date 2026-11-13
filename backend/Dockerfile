# API image. Built from the repository root: docker build -f backend/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv

FROM python:3.15.0-slim-trixie@sha256:f5b92f2a669a4c347044f29367f31983280b648250ca94dea66f98c53bc87e58

# git clones the repositories to index.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin repolens

WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv uv sync --locked --no-dev

COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
COPY backend/repolens ./repolens

USER repolens
EXPOSE 8000
# Migrations run on every start; Alembic skips the ones already applied.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn repolens.main:app --host 0.0.0.0 --port 8000"]
