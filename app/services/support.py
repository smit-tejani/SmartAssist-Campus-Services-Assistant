# app/services/support.py
"""
Support service layer for handling ticket and appointment saving.
Provides MongoDB persistence and GridFS file handling.
"""

from datetime import datetime
from bson import ObjectId
from fastapi import UploadFile
from app.db.mongo import fs, tickets_collection, appointments_collection


def save_ticket(ticket_data: dict, attachment: UploadFile | None = None) -> str:
    """
    Save a support ticket document to MongoDB and optionally upload its attachment.
    Returns the inserted ticket's ObjectId as a string.
    """
    try:
        if attachment:
            file_id = fs.put(
                attachment.file.read(),
                filename=attachment.filename,
                content_type=attachment.content_type or "application/octet-stream",
            )
            ticket_data["attachment_id"] = file_id

        result = tickets_collection.insert_one(ticket_data)
        return str(result.inserted_id)
    except Exception as exc:
        print(f"[ERROR] save_ticket: {exc}")
        raise


def save_appointment(appointment_data: dict, attachment: UploadFile | None = None) -> str:
    """
    Save appointment data and optional attachment to MongoDB.
    Returns the inserted appointment's ObjectId as a string.
    """
    try:
        if attachment:
            file_id = fs.put(
                attachment.file.read(),
                filename=attachment.filename,
                content_type=attachment.content_type or "application/octet-stream",
            )
            appointment_data["attachment_id"] = file_id

        result = appointments_collection.insert_one(appointment_data)
        return str(result.inserted_id)
    except Exception as exc:
        print(f"[ERROR] save_appointment: {exc}")
        raise
