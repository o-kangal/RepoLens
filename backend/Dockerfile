# API image. Built from the repository root: docker build -f backend/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

FROM python:3.12.16-slim-trixie@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

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
