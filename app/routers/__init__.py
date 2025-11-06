from fastapi import APIRouter, FastAPI

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


def register_routers(app: FastAPI) -> None:
    app.include_router(diagnostics.router)
    app.include_router(live_chat.router)
    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(chatbot.router)
    app.include_router(support.router)
    app.include_router(students.router)
    app.include_router(staff.router)
    app.include_router(departments.router)
    app.include_router(notifications.router)
    app.include_router(events.router)
    app.include_router(surveys.router)
