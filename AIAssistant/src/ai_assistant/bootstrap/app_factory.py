"""FastAPI composition boundary for the platform."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..settings import ArchitectureSettings


def create_app(settings: ArchitectureSettings, lifespan: Callable[..., Any]) -> FastAPI:
    app = FastAPI(title="3D-Reconstruction AI Server", version="3.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["POST", "GET", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
        expose_headers=["Mcp-Session-Id"],
    )
    return app
