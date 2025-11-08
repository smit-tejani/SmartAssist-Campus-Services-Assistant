from __future__ import annotations

from typing import Any, Dict

import gridfs
from pymongo import MongoClient

from app.core.config import settings


client = MongoClient(settings.mongodb_uri)
db = client.smartassist
users_collection = db.users
live_chat_collection = db.live_chat
live_chat_sessions = db.live_chat_sessions
kb_collection = db.knowledge_base
notifications_collection = db.notifications
events_collection = db.events
surveys_collection = db.surveys
courses_collection = db.courses
registrations_collection = db.registrations
students_collection = db.students
departments_collection = db.departments
appointments_collection = db.appointments
tickets_collection = db.tickets
fs = gridfs.GridFS(db)


def ensure_indexes() -> None:
    try:
        kb_collection.create_index(
            [("title", "text"), ("content", "text"), ("category", "text")]
        )
    except Exception:
        pass


ensure_indexes()


def as_dict(document: Dict[str, Any]) -> Dict[str, Any]:
    return document
