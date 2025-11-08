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
    support,
    surveys,
)

__all__: List[str] = [
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
    routers = [
        diagnostics.router,
        live_chat.router,
        pages.router,
        auth.router,
        chatbot.router,
        support.router,
        students.router,
        staff.router,
        departments.router,
        notifications.router,
        events.router,
        surveys.router,
    ]
    for r in routers:
        app.include_router(r)
