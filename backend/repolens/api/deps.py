"""Services the routers share, created in the application lifespan."""

from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from repolens.ingest.clone import clone_github
from repolens.ingest.pipeline import Clone
from repolens.providers.embeddings import Embedder


@dataclass(frozen=True, slots=True)
class Services:
    session_factory: sessionmaker[Session]
    embedder: Embedder | None
    """None when no embedding provider is configured."""


def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return get_services(request).session_factory


def get_embedder(request: Request) -> Embedder:
    embedder = get_services(request).embedder
    if embedder is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "no embedding provider is configured"
        )
    return embedder


def get_clone() -> Clone:
    return clone_github
