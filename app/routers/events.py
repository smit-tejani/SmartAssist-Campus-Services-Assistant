from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db.mongo import events_collection
from app.dependencies.auth import get_current_user
from app.services.notifications import _create_event_notifications, _notify_event_completed

router = APIRouter()


class EventCreate(BaseModel):
    title: str
    description: str
    event_date: str
    event_time: str | None = None
    priority: str = "normal"
    target_audience: str = "all"
    specific_emails: list[str] | None = None
    category: str = "general"


@router.post("/api/events/create")
async def create_event(event: EventCreate, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can create events")

    event_doc = {
        "title": event.title,
        "description": event.description,
        "event_date": event.event_date,
        "event_time": event.event_time,
        "priority": event.priority,
        "target_audience": event.target_audience,
        "specific_emails": event.specific_emails,
        "category": event.category,
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "created_by": user.get("email"),
        "created_by_name": user.get("full_name"),
    }

    result = events_collection.insert_one(event_doc)
    event_id = str(result.inserted_id)

    await _create_event_notifications(event_doc, event_id)

    return {
        "success": True,
        "event_id": event_id,
        "message": "Event created and notifications sent",
    }


@router.get("/api/events")
async def get_events(status: str | None = None, audience: str | None = None):
    query = {}
    if status:
        query["status"] = status
    if audience:
        query["target_audience"] = audience

    events = list(events_collection.find(query).sort("event_date", 1))
    for event in events:
        event["_id"] = str(event["_id"])
    return events


@router.put("/api/events/{event_id}")
async def update_event(event_id: str, request: Request, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can update events")

    data = await request.json()
    updates = {"updated_at": datetime.utcnow().isoformat()}
    for field in ["title", "description", "event_date", "event_time", "priority", "target_audience", "specific_emails", "category", "status"]:
        if field in data:
            updates[field] = data[field]

    result = events_collection.update_one({"_id": ObjectId(event_id)}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    event = events_collection.find_one({"_id": ObjectId(event_id)})
    event["_id"] = str(event["_id"])
    return event


@router.delete("/api/events/{event_id}")
async def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can delete events")

    result = events_collection.delete_one({"_id": ObjectId(event_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"success": True, "message": "Event deleted"}


@router.put("/api/events/{event_id}/complete")
async def mark_event_complete(event_id: str, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can complete events")

    result = events_collection.update_one(
        {"_id": ObjectId(event_id)},
        {"$set": {"status": "completed", "completed_at": datetime.utcnow().isoformat()}},
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    event = events_collection.find_one({"_id": ObjectId(event_id)})
    await _notify_event_completed(event, event_id)
    return {"success": True, "message": "Event marked as completed"}
