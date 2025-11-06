from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db.mongo import notifications_collection
from app.dependencies.auth import get_current_user

router = APIRouter()


class NotificationCreate(BaseModel):
    user_email: str
    type: str
    title: str
    message: str
    priority: str = "normal"
    related_id: str | None = None
    link: str | None = None


@router.post("/api/notifications/create")
async def create_notification(notification: NotificationCreate, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["admin", "staff"]:
        raise HTTPException(status_code=403, detail="Only admin and staff can create notifications")

    notification_doc = {
        "user_email": notification.user_email,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "priority": notification.priority,
        "related_id": notification.related_id,
        "link": notification.link,
        "status": "unread",
        "created_at": datetime.now().isoformat(),
        "created_by": user.get("email"),
    }

    result = notifications_collection.insert_one(notification_doc)
    return {
        "success": True,
        "notification_id": str(result.inserted_id),
        "message": "Notification created successfully",
    }


@router.get("/api/notifications")
async def get_notifications(user: dict = Depends(get_current_user), status: str | None = None):
    user_email = user.get("email")

    query = {"user_email": user_email}
    if status:
        query["status"] = status

    notifications = list(
        notifications_collection.find(query).sort("created_at", -1)
    )
    for notification in notifications:
        notification["_id"] = str(notification["_id"])
    return notifications


@router.put("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(get_current_user)):
    user_email = user.get("email")

    result = notifications_collection.update_one(
        {"_id": ObjectId(notification_id), "user_email": user_email},
        {"$set": {"status": "read", "read_at": datetime.now().isoformat()}},
    )

    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"success": True, "message": "Notification marked as read"}


@router.put("/api/notifications/mark-all-read")
async def mark_all_notifications_read(user: dict = Depends(get_current_user)):
    user_email = user.get("email")

    result = notifications_collection.update_many(
        {"user_email": user_email, "status": "unread"},
        {"$set": {"status": "read", "read_at": datetime.now().isoformat()}},
    )

    return {
        "success": True,
        "message": f"Marked {result.modified_count} notifications as read",
        "count": result.modified_count,
    }


@router.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: str, user: dict = Depends(get_current_user)):
    user_email = user.get("email")

    result = notifications_collection.delete_one(
        {"_id": ObjectId(notification_id), "user_email": user_email}
    )

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")

    return {"success": True, "message": "Notification deleted"}


@router.get("/api/notifications/unread/count")
async def get_unread_count(user: dict = Depends(get_current_user)):
    user_email = user.get("email")

    count = notifications_collection.count_documents(
        {"user_email": user_email, "status": "unread"}
    )
    return {"count": count}
