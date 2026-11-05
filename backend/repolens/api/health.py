"""Liveness endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api")


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


@router.get("/health")
def health() -> HealthResponse:
    return HealthResponse()
