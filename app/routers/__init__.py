# app/routers/__init__.py
from typing import List

from fastapi import FastAPI

# import router modules
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

# define public exports
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
    """
    Register routers on the FastAPI app in a predictable order.
    Keep the order stable to avoid accidental route masking.
    """
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
