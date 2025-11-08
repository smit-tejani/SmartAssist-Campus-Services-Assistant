from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.db.mongo import notifications_collection


async def _create_appointment_notification(appointment: dict, appointment_id: str, action: str) -> None:
    notification = {
        "type": "appointment",
        "action": action,
        "appointment_id": appointment_id,
        "student_email": appointment.get("student_email"),
        "assigned_staff": appointment.get("assigned_staff"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": f"Appointment {action.title()}",
        "message": f"Appointment '{appointment.get('subject', 'No Subject')}' has been {action}",
    }
    notifications_collection.insert_one(notification)


async def _create_ticket_notification(ticket: dict, ticket_id: str, action: str) -> None:
    notification = {
        "type": "ticket",
        "action": action,
        "ticket_id": ticket_id,
        "student_email": ticket.get("student_email"),
        "assigned_staff": ticket.get("assigned_staff"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": f"Ticket {action.title()}",
        "message": f"Ticket '{ticket.get('subject', 'No Subject')}' has been {action}",
    }
    notifications_collection.insert_one(notification)


async def _notify_admin_new_ticket(ticket: dict, ticket_id: str) -> None:
    notification = {
        "type": "ticket",
        "action": "created",
        "ticket_id": ticket_id,
        "student_email": ticket.get("student_email"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "New Ticket Created",
        "message": f"New ticket '{ticket.get('subject', 'No Subject')}' created by {ticket.get('student_name', 'Unknown Student')}.",
        "recipients": ["admin"],
    }
    notifications_collection.insert_one(notification)


async def _notify_staff_ticket_closed(ticket: dict, ticket_id: str, closed_by_email: Optional[str] = None) -> None:
    if not ticket.get("assigned_staff"):
        return
    notification = {
        "type": "ticket",
        "action": "closed",
        "ticket_id": ticket_id,
        "student_email": ticket.get("student_email"),
        "assigned_staff": ticket.get("assigned_staff"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "Ticket Closed",
        "message": f"Ticket '{ticket.get('subject', 'No Subject')}' has been closed.",
        "closed_by": closed_by_email,
    }
    notifications_collection.insert_one(notification)


async def _notify_admin_ticket_resolved(ticket: dict, ticket_id: str) -> None:
    notification = {
        "type": "ticket",
        "action": "resolved",
        "ticket_id": ticket_id,
        "student_email": ticket.get("student_email"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "Ticket Resolved",
        "message": f"Ticket '{ticket.get('subject', 'No Subject')}' has been resolved.",
        "recipients": ["admin"],
    }
    notifications_collection.insert_one(notification)


async def _notify_admin_appointment_scheduled(appointment: dict, appointment_id: str) -> None:
    notification = {
        "type": "appointment",
        "action": "scheduled",
        "appointment_id": appointment_id,
        "student_email": appointment.get("student_email"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "New Appointment Scheduled",
        "message": f"Appointment '{appointment.get('subject', 'No Subject')}' scheduled for {appointment.get('date')} {appointment.get('time_slot')}.",
        "recipients": ["admin"],
    }
    notifications_collection.insert_one(notification)


async def _notify_staff_appointment_scheduled(appointment: dict, appointment_id: str) -> None:
    if not appointment.get("assigned_staff"):
        return
    notification = {
        "type": "appointment",
        "action": "scheduled",
        "appointment_id": appointment_id,
        "student_email": appointment.get("student_email"),
        "assigned_staff": appointment.get("assigned_staff"),
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "New Appointment Assigned",
        "message": f"You have been assigned appointment '{appointment.get('subject', 'No Subject')}'",
    }
    notifications_collection.insert_one(notification)


async def _notify_event_completed(event: dict, event_id: str) -> None:
    notification = {
        "type": "event",
        "action": "completed",
        "event_id": event_id,
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "Event Completed",
        "message": f"Event '{event.get('title', 'No Title')}' has been completed.",
    }
    notifications_collection.insert_one(notification)


async def _create_event_notifications(event: dict, event_id: str) -> None:
    notification = {
        "type": "event",
        "action": "created",
        "event_id": event_id,
        "created_at": datetime.utcnow(),
        "status": "unread",
        "title": "New Event",
        "message": f"New event '{event.get('title', 'No Title')}' scheduled on {event.get('date')} {event.get('time')}.",
    }
    notifications_collection.insert_one(notification)


async def _notify_survey_available(survey: dict, survey_id: str) -> None:
    notification = {
        "type": "survey",
        "action": "published",
        "survey_id": survey_id,
        "title": f"Survey Available: {survey.get('title', 'Untitled Survey')}",
        "message": survey.get("description", ""),
        "status": "unread",
        "created_at": datetime.utcnow(),
        "recipients": ["student"],
    }
    notifications_collection.insert_one(notification)


__all__ = [
    "_create_appointment_notification",
    "_create_ticket_notification",
    "_notify_admin_new_ticket",
    "_notify_staff_ticket_closed",
    "_notify_admin_ticket_resolved",
    "_notify_admin_appointment_scheduled",
    "_notify_staff_appointment_scheduled",
    "_notify_event_completed",
    "_create_event_notifications",
    "_notify_survey_available",
]
