from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.core.config import settings
from app.core.templates import templates  # noqa: F401 ensures templates are loaded
from app.routers import register_routers


def create_app() -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory="static"), name="static")

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        same_site="lax",
        https_only=False,
        max_age=3600,
        session_cookie=settings.session_cookie,
    )

    register_routers(app)
    return app


app = create_app()
