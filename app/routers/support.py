from __future__ import annotations

import io
from datetime import date, datetime
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.db.mongo import (
    appointments_collection,
    db,
    fs,
    kb_collection,
    tickets_collection,
    users_collection,
)
from app.dependencies.auth import get_current_user
from app.services.notifications import (
    _create_appointment_notification,
    _create_ticket_notification,
    _notify_admin_appointment_scheduled,
    _notify_admin_new_ticket,
    _notify_admin_ticket_resolved,
    _notify_staff_appointment_scheduled,
    _notify_staff_ticket_closed,
)
from app.services.support import save_appointment, save_ticket

router = APIRouter()


class TicketCreateRequest(BaseModel):
    subject: str
    category: str
    priority: str
    description: str
    student_name: str = ""
    student_email: str = ""


@router.post("/raise_ticket")
async def raise_ticket(
    subject: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    description: str = Form(...),
    student_email: str = Form(...),
    student_name: str = Form(...),
    preferred_staff: str = Form(""),
    attachment: UploadFile | None = File(None),
):
    if not subject or not category or not priority or not description:
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)

    if not student_email or not student_name:
        return JSONResponse({"success": False, "error": "Student information missing"}, status_code=400)

    ticket = {
        "student_email": student_email,
        "student_name": student_name,
        "subject": subject,
        "category": category,
        "priority": priority,
        "description": description,
        "status": "Open",
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "assigned_staff": None,
        "assigned_to_name": None,
    }

    if preferred_staff == "auto-assign-admin":
        admin_user = users_collection.find_one({"role": "admin"})
        if admin_user:
            ticket["assigned_staff"] = admin_user.get("email")
            ticket["assigned_to_name"] = admin_user.get("full_name", admin_user.get("email"))
            ticket["assigned_at"] = datetime.now().isoformat()
            ticket["preferred_staff"] = None
            ticket["preferred_staff_name"] = None
        else:
            ticket["preferred_staff"] = None
            ticket["preferred_staff_name"] = None
    elif preferred_staff:
        staff_member = users_collection.find_one({"email": preferred_staff})
        if staff_member:
            ticket["preferred_staff"] = preferred_staff
            ticket["preferred_staff_name"] = staff_member.get("full_name", preferred_staff)
        else:
            ticket["preferred_staff"] = preferred_staff
            ticket["preferred_staff_name"] = preferred_staff

    try:
        inserted_id = save_ticket(ticket, attachment)
        await _notify_admin_new_ticket(ticket, str(inserted_id))
        return {"success": True, "ticket_id": str(inserted_id)}
    except Exception as exc:
        print(f"[ERROR] /raise_ticket exception: {exc}")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/book_appointment")
async def book_appointment(
    department: str = Form(...),
    assigned_staff: str = Form(...),
    subject: str = Form(...),
    date: str = Form(...),
    time_slot: str = Form(...),
    meeting_mode: str = Form(...),
    notes: str = Form(""),
    student_email: str = Form(...),
    student_name: str = Form(...),
    attachment: UploadFile | None = File(None),
):
    if not all([department, assigned_staff, subject, date, time_slot, meeting_mode]):
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)

    if not student_email or not student_name:
        return JSONResponse({"success": False, "error": "Student information missing"}, status_code=400)

    if assigned_staff == "auto-assign-admin":
        admin_user = users_collection.find_one({"role": "admin"})
        if admin_user:
            assigned_staff = admin_user.get("email")
            assigned_staff_name = admin_user.get("full_name", admin_user.get("email"))
        else:
            return JSONResponse({"success": False, "error": "Admin user not found"}, status_code=500)
    else:
        staff_member = users_collection.find_one({"email": assigned_staff})
        assigned_staff_name = staff_member.get("full_name") if staff_member else assigned_staff

    appt = {
        "student_email": student_email,
        "student_name": student_name,
        "department": department,
        "subject": subject,
        "date": date,
        "time_slot": time_slot,
        "meeting_mode": meeting_mode,
        "notes": notes,
        "status": "Pending",
        "created_at": datetime.now().isoformat(),
        "last_updated": datetime.now().isoformat(),
        "assigned_staff": assigned_staff,
        "assigned_staff_name": assigned_staff_name,
        "location_mode": "To be assigned",
        "confirmation_status": "Awaiting Confirmation",
    }

    try:
        inserted_id = save_appointment(appt, attachment)
        await _notify_admin_appointment_scheduled(appt, str(inserted_id))
        await _notify_staff_appointment_scheduled(appt, str(inserted_id))
        return {"success": True, "appointment_id": str(inserted_id)}
    except Exception as exc:
        print(f"[ERROR] /book_appointment exception: {exc}")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/api/tickets/cancel/{ticket_id}")
async def cancel_ticket(ticket_id: str):
    try:
        result = tickets_collection.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": {"status": "Cancelled", "last_updated": datetime.now().isoformat()}},
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Ticket cancelled successfully."}
        return {"success": False, "message": "Ticket not found or already cancelled."}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/appointments/cancel/{appointment_id}")
async def cancel_appointment(appointment_id: str):
    try:
        result = appointments_collection.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": {"status": "Cancelled"}},
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Appointment cancelled successfully."}
        return {"success": False, "message": "Appointment not found or already cancelled."}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/api/appointments/reschedule/{appointment_id}")
async def reschedule_appointment(appointment_id: str, new_date: str, new_time: str):
    try:
        result = appointments_collection.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": {"date": new_date, "time": new_time, "status": "Pending Confirmation"}},
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Appointment rescheduled successfully."}
        return {"success": False, "message": "Appointment not found or could not be rescheduled."}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/debug")
async def api_debug():
    try:
        stats = {
            "tickets": tickets_collection.count_documents({}),
            "appointments": appointments_collection.count_documents({}),
            "users": users_collection.count_documents({}),
            "knowledge_base": kb_collection.count_documents({}),
        }
        return {"status": "ok", "stats": stats}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/tickets")
async def api_tickets(status: str | None = None, student_email: str | None = None):
    query = {}
    if status:
        query["status"] = status
    if student_email:
        query["student_email"] = student_email
    tickets = list(tickets_collection.find(query).sort("created_at", -1))
    for ticket in tickets:
        ticket["_id"] = str(ticket["_id"])
        if "attachment_id" in ticket:
            ticket["attachment_id"] = str(ticket["attachment_id"])
    return tickets


@router.get("/api/appointments")
async def api_appointments(
    upcoming: bool = False,
    student_email: str | None = None,
    admin: bool = False,
):
    query = {}
    if student_email:
        query["student_email"] = student_email
    if upcoming:
        query["date"] = {"$gte": date.today().isoformat()}
        query["status"] = {"$ne": "Cancelled"}
    appointments = list(appointments_collection.find(query).sort("date", 1))
    for appt in appointments:
        appt["_id"] = str(appt["_id"])
        if "attachment_id" in appt:
            appt["attachment_id"] = str(appt["attachment_id"])
    return appointments


@router.get("/api/attachment/{file_id}")
async def api_attachment(file_id: str):
    try:
        grid_out = fs.get(ObjectId(file_id))
        data = grid_out.read()
        return StreamingResponse(
            io.BytesIO(data),
            media_type=(grid_out.content_type or "application/octet-stream"),
            headers={"Content-Disposition": f"attachment; filename=\"{grid_out.filename or file_id}\""},
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


@router.get("/api/user")
async def get_user_details(request: Request):
    try:
        user = request.session.get("user")
        if user:
            return {
                "full_name": user.get("full_name"),
                "email": user.get("email"),
                "role": user.get("role"),
            }
        return JSONResponse({"error": "User not logged in"}, status_code=401)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/stats")
async def get_stats():
    try:
        knowledge_articles_count = kb_collection.count_documents({})
        departments_count = db.departments.count_documents({"status": "active"})
        total_users_count = users_collection.count_documents({})
        upcoming_appointments_count = appointments_collection.count_documents(
            {"status": {"$ne": "Cancelled"}, "date": {"$gte": date.today().isoformat()}}
        )

        return {
            "knowledge_articles": knowledge_articles_count,
            "departments": departments_count,
            "total_users": total_users_count,
            "upcoming_appointments": upcoming_appointments_count,
        }
    except Exception as exc:
        print(f"Error fetching stats: {exc}")
        return {
            "knowledge_articles": 0,
            "departments": 0,
            "total_users": 0,
            "upcoming_appointments": 0,
        }


@router.get("/api/knowledge_base")
async def get_knowledge_base():
    try:
        articles = list(kb_collection.find({}, {"_id": 0}))
        return {"articles": articles}
    except Exception as exc:
        print(f"Error fetching knowledge base articles: {exc}")
        return {"articles": []}


@router.post("/api/knowledge_base")
async def add_knowledge_article(request: Request):
    data = await request.json()
    category = data.get("category")
    title = data.get("title")
    url = data.get("url")

    if not category or not title or not url:
        return JSONResponse({"error": "All fields are required."}, status_code=400)

    try:
        from extract_web_content_to_mongo import extract_page, save_to_db

        article = extract_page(url, category, title)
        if not article:
            return JSONResponse({"error": "Failed to fetch content from URL."}, status_code=400)

        save_to_db(article)
        return JSONResponse({"message": "Article added successfully."}, status_code=201)
    except Exception as exc:
        print(f"Error adding article: {exc}")
        return JSONResponse({"error": "Internal server error."}, status_code=500)


@router.post("/api/tickets")
async def create_ticket(ticket: TicketCreateRequest, user: dict = Depends(get_current_user)):
    try:
        student_email = user.get("email", "")
        student_name = user.get("full_name", "")

        ticket_doc = {
            "student_email": student_email,
            "student_name": student_name,
            "subject": ticket.subject,
            "category": ticket.category,
            "priority": ticket.priority,
            "description": ticket.description,
            "status": "Open",
            "created_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat(),
            "assigned_staff": None,
            "assigned_to_name": None,
        }

        result = tickets_collection.insert_one(ticket_doc)

        await _create_ticket_notification(ticket_doc, str(result.inserted_id), "created")

        return {
            "success": True,
            "ticket_id": str(result.inserted_id),
            "message": "Ticket created successfully",
        }
    except Exception as exc:
        print(f"Error creating ticket: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    try:
        ticket = tickets_collection.find_one({"_id": ObjectId(ticket_id)})
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        ticket["_id"] = str(ticket["_id"])
        ticket["date_created"] = ticket.get("date_created", ticket.get("created_at", "Unknown"))
        ticket["last_updated"] = ticket.get("last_updated", "Unknown")

        if "attachment_id" in ticket:
            ticket["attachment_id"] = str(ticket["attachment_id"])

        return ticket
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str):
    try:
        appointment = appointments_collection.find_one({"_id": ObjectId(appointment_id)})
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        appointment["_id"] = str(appointment["_id"])

        return appointment
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/api/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, staff_email: str):
    try:
        staff = users_collection.find_one({"email": staff_email, "role": "staff"})
        if not staff:
            raise HTTPException(status_code=404, detail="Staff member not found")

        result = tickets_collection.update_one(
            {"_id": ObjectId(ticket_id)},
            {
                "$set": {
                    "assigned_to": staff_email,
                    "assigned_to_name": staff.get("full_name"),
                    "status": "assigned",
                    "assigned_at": datetime.now().isoformat(),
                }
            },
        )

        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Ticket not found")

        return {"message": "Ticket assigned successfully"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/api/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        ticket = tickets_collection.find_one({"_id": ObjectId(ticket_id)})
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")

        data = await request.json()
        status = data.get("status")
        assigned_staff = data.get("assigned_staff")

        update_fields = {
            "last_updated": datetime.now().isoformat(),
        }

        notification_action = None

        if status:
            update_fields["status"] = status
            if status.lower() == "resolved":
                notification_action = "resolved"
            elif status.lower() == "closed":
                notification_action = "closed"

        if assigned_staff:
            staff_member = users_collection.find_one({"email": assigned_staff})
            if staff_member:
                update_fields["assigned_staff"] = assigned_staff
                update_fields["assigned_to_name"] = staff_member.get("full_name", assigned_staff)
            else:
                update_fields["assigned_staff"] = assigned_staff
                update_fields["assigned_to_name"] = assigned_staff

        tickets_collection.update_one({"_id": ObjectId(ticket_id)}, {"$set": update_fields})

        updated_ticket = tickets_collection.find_one({"_id": ObjectId(ticket_id)})
        await _create_ticket_notification(updated_ticket, ticket_id, "updated")

        if notification_action == "resolved":
            await _notify_admin_ticket_resolved(updated_ticket, ticket_id)
        elif notification_action == "closed":
            await _notify_staff_ticket_closed(updated_ticket, ticket_id, user.get("email"))

        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/api/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, request: Request):
    try:
        appointment = appointments_collection.find_one({"_id": ObjectId(appointment_id)})
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")

        data = await request.json()
        updates = {"last_updated": datetime.now().isoformat()}

        for field in ["status", "assigned_staff", "assigned_staff_name", "location_mode", "confirmation_status", "notes"]:
            if field in data:
                updates[field] = data[field]

        appointments_collection.update_one({"_id": ObjectId(appointment_id)}, {"$set": updates})

        updated_appt = appointments_collection.find_one({"_id": ObjectId(appointment_id)})
        await _create_appointment_notification(updated_appt, appointment_id, "updated")

        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.put("/api/appointments/{appointment_id}/confirm")
async def confirm_appointment(appointment_id: str):
    try:
        result = appointments_collection.update_one(
            {"_id": ObjectId(appointment_id)},
            {
                "$set": {
                    "status": "Confirmed",
                    "confirmation_status": "Confirmed",
                    "confirmed_at": datetime.utcnow().isoformat(),
                }
            },
        )
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")

        appointment = appointments_collection.find_one({"_id": ObjectId(appointment_id)})
        await _create_appointment_notification(appointment, appointment_id, "confirmed")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
