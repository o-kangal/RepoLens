"""FastAPI application."""

from fastapi import FastAPI

from repolens.api.health import router as health_router
from repolens.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings()
    application = FastAPI(title="RepoLens", debug=resolved.debug)
    application.include_router(health_router)
    return application


app = create_app()
