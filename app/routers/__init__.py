from typing import List
from fastapi import FastAPI

from app.routers import (
    auth,
    chatbot,
    diagnostics,
    departments,
    events,
    live_chat,
    notifications,
    pages,
    staff,
    students,
    support,   # support.py (tickets)
    surveys,
)
from app.routers.support import router as support_nested_router


__all__ = [
    "auth",
    "chatbot",
    "diagnostics",
    "departments",
    "events",
    "live_chat",
    "notifications",
    "pages",
    "staff",
    "students",
    "support",
    "surveys",
    "register_routers",
]


def register_routers(app: FastAPI) -> None:
    """Attach all routers in a clean, deterministic order."""
    routers: List = [
        diagnostics.router,
        live_chat.router,
        pages.router,
        auth.router,
        chatbot.router,
        support.router,
        support_nested_router,
        students.router,
        staff.router,
        departments.router,
        notifications.router,
        events.router,
        surveys.router,
    ]
    for r in routers:
        app.include_router(r)
