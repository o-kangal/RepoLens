"""FastAPI application."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
from fastapi import FastAPI

from repolens.api.deps import Services
from repolens.api.health import router as health_router
from repolens.api.repos import router as repos_router
from repolens.config import Settings
from repolens.db.session import create_db_engine, create_session_factory
from repolens.ingest.pipeline import fail_interrupted_jobs
from repolens.providers.embeddings import VoyageEmbedder

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(resolved)
        session_factory = create_session_factory(engine)
        # Indexing jobs run inside this process, so any job still marked running was cut off.
        interrupted = await asyncio.to_thread(fail_interrupted_jobs, session_factory)
        if interrupted:
            logger.warning("marked %d interrupted indexing jobs as failed", interrupted)
        async with httpx2.AsyncClient() as http:
            embedder = (
                VoyageEmbedder(
                    http=http, api_key=resolved.voyage_api_key, model=resolved.embedding_model
                )
                if resolved.voyage_api_key is not None
                else None
            )
            application.state.services = Services(
                session_factory=session_factory, embedder=embedder
            )
            yield
        engine.dispose()

    application = FastAPI(title="RepoLens", debug=resolved.debug, lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(repos_router)
    return application


app = create_app()
