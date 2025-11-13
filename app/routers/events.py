from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.db.mongo import events_collection, users_collection
from app.dependencies.auth import get_current_user
from app.services.notifications import _create_event_notifications, _notify_event_completed

router = APIRouter()


class EventCreate(BaseModel):
    """Schema used when creating a new event.

    Additional fields such as ``location`` and ``seats_total`` allow events
    to include a physical location and seat capacity. ``seats_available``
    will be derived from ``seats_total`` on creation. If no seat capacity
    is provided, the event is considered unbounded (``seats_available`` is None).
    """

    title: str
    description: str
    event_date: str
    event_time: str | None = None
    priority: str = "normal"
    target_audience: str = "all"
    specific_emails: list[str] | None = None
    category: str = "general"
    # New optional fields
    location: str | None = None
    seats_total: int | None = None

    # ``seats_available`` is optional on creation. If provided, it must not
    # exceed ``seats_total``; otherwise it will default to ``seats_total``.
    seats_available: int | None = None


@router.post("/api/events/create")
async def create_event(event: EventCreate, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can create events")

    # Determine seats_available: if explicitly provided, ensure it does not exceed
    # seats_total (if seats_total exists). Otherwise default seats_available
    # to seats_total. If seats_total is None, seats_available remains None.
    seats_available: int | None = None
    if event.seats_total is not None:
        if event.seats_available is not None:
            if event.seats_available > event.seats_total:
                raise HTTPException(status_code=400, detail="seats_available cannot exceed seats_total")
            seats_available = event.seats_available
        else:
            seats_available = event.seats_total

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
        "location": event.location,
        "seats_total": event.seats_total,
        "seats_available": seats_available,
        # Track registered users by their email or id. This will allow RSVP
        "registrants": [],
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
    # Fields that can be updated by admin/staff. We include new fields like
    # location, seats_total and seats_available. Registrants cannot be
    # arbitrarily overwritten via this endpoint.
    for field in [
        "title",
        "description",
        "event_date",
        "event_time",
        "priority",
        "target_audience",
        "specific_emails",
        "category",
        "status",
        "location",
        "seats_total",
        "seats_available",
    ]:
        if field in data:
            updates[field] = data[field]

    result = events_collection.update_one({"_id": ObjectId(event_id)}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Event not found")

    event = events_collection.find_one({"_id": ObjectId(event_id)})
    event["_id"] = str(event["_id"])
    return event


@router.get("/api/events/{event_id}")
async def get_event_detail(event_id: str, user: dict = Depends(get_current_user)):
    """
    Retrieve full details of a single event.

    This endpoint returns the event document, including seats and a count of
    registrants. It can be used by the student UI to show event details before
    registering.
    """
    event = events_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    event["_id"] = str(event["_id"])
    # Provide a registrant_count field for convenience
    event["registrant_count"] = len(event.get("registrants", []))
    return event


@router.post("/api/events/{event_id}/register")
async def register_for_event(event_id: str, user: dict = Depends(get_current_user)):
    """
    Register the current user for an event.

    - If ``seats_available`` is not None and is zero, registration fails.
    - If the user is already registered, return gracefully.
    - On success, decrement ``seats_available`` and add the user email to
      ``registrants``.
    """
    # Ensure event exists
    event = events_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Invalid user")

    # Check if already registered
    registrants = event.get("registrants", [])
    if user_email in registrants:
        return {"success": True, "message": "Already registered"}

    # Check seat availability
    seats_available = event.get("seats_available")
    if seats_available is not None and seats_available <= 0:
        raise HTTPException(status_code=400, detail="No seats available")

    # Atomically register user and decrement seats_available if applicable
    update_query = {"_id": ObjectId(event_id), "registrants": {"$ne": user_email}}
    update_doc: dict[str, Any] = {"$push": {"registrants": user_email}}
    if seats_available is not None:
        update_doc["$inc"] = {"seats_available": -1}
    result = events_collection.update_one(update_query, update_doc)
    if result.modified_count == 0:
        # Either user already registered or event changed concurrently
        raise HTTPException(status_code=409, detail="Could not register for event")
    return {"success": True, "message": "Registered successfully"}


@router.delete("/api/events/{event_id}/register")
async def unregister_from_event(event_id: str, user: dict = Depends(get_current_user)):
    """
    Remove the current user's registration from an event.

    If ``seats_available`` is being tracked, increment it upon successful removal.
    """
    event = events_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    user_email = user.get("email")
    if not user_email:
        raise HTTPException(status_code=400, detail="Invalid user")

    seats_available = event.get("seats_available")

    update_query = {"_id": ObjectId(event_id), "registrants": user_email}
    update_doc: dict[str, Any] = {"$pull": {"registrants": user_email}}
    if seats_available is not None:
        update_doc["$inc"] = {"seats_available": 1}
    result = events_collection.update_one(update_query, update_doc)
    if result.modified_count == 0:
        raise HTTPException(status_code=409, detail="Not registered for event")
    return {"success": True, "message": "Unregistered successfully"}


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


# --------------------------------------------------------------------------
# Retrieve registrant details for an event (admin/staff only)
# --------------------------------------------------------------------------
@router.get("/api/events/{event_id}/registrants")
async def get_event_registrants(event_id: str, user: dict = Depends(get_current_user)):
    """
    Return the list of registrants for a given event.

    This endpoint is restricted to admin and staff roles.  For each registered
    email, it attempts to look up the corresponding user in the users
    collection to provide a full name if available.  If no matching user is
    found, the email alone is returned.
    """
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can view registrants")

    event = events_collection.find_one({"_id": ObjectId(event_id)})
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    registrants: list[str] = event.get("registrants", []) or []
    if not registrants:
        return []

    # Fetch user info for registrant emails
    users_cursor = users_collection.find({"email": {"$in": registrants}}, {"email": 1, "full_name": 1})
    users_map = {u.get("email"): u.get("full_name") for u in users_cursor}
    details = []
    for email in registrants:
        details.append({
            "email": email,
            "full_name": users_map.get(email, email)
        })
    return details
