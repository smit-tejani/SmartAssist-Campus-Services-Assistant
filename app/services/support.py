from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import UploadFile

from app.db.mongo import appointments_collection, fs, tickets_collection


def save_ticket(ticket: dict, attachment: Optional[UploadFile] = None):
    if attachment is not None:
        try:
            content = attachment.file.read()
            file_id = fs.put(content, filename=attachment.filename, contentType=attachment.content_type)
            ticket["attachment_id"] = file_id
            ticket["attachment_name"] = attachment.filename
            ticket["attachment_content_type"] = attachment.content_type
        except Exception as exc:
            ticket["attachment_error"] = f"failed to save to gridfs: {exc}"

    result = tickets_collection.insert_one(ticket)
    inserted_id = result.inserted_id

    debug_doc = ticket.copy()
    if "attachment_id" in debug_doc:
        debug_doc["attachment_id"] = str(debug_doc["attachment_id"])
    try:
        print(f"[DEBUG] Inserted ticket id: {inserted_id}")
        print(f"[DEBUG] Ticket document: {json.dumps(debug_doc, default=str)}")
    except Exception:
        print("[DEBUG] Ticket document (fallback):", debug_doc)
    return inserted_id


def save_appointment(appt: dict, attachment: Optional[UploadFile] = None):
    if attachment is not None:
        try:
            content = attachment.file.read()
            file_id = fs.put(content, filename=attachment.filename, contentType=attachment.content_type)
            appt["attachment_id"] = file_id
            appt["attachment_name"] = attachment.filename
            appt["attachment_content_type"] = attachment.content_type
        except Exception as exc:
            appt["attachment_error"] = f"failed to save to gridfs: {exc}"

    result = appointments_collection.insert_one(appt)
    inserted_id = result.inserted_id

    debug_doc = appt.copy()
    if "attachment_id" in debug_doc:
        debug_doc["attachment_id"] = str(debug_doc["attachment_id"])
    try:
        print(f"[DEBUG] Inserted appointment id: {inserted_id}")
        print(f"[DEBUG] Appointment document: {json.dumps(debug_doc, default=str)}")
    except Exception:
        print("[DEBUG] Appointment document (fallback):", debug_doc)
    return inserted_id


__all__ = ["save_ticket", "save_appointment"]
