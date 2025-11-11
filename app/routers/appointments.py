# app/routers/appointments.py
from datetime import datetime, date
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Form, File, UploadFile, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.db.mongo import (
    appointments_collection,
    db,
    fs,
    kb_collection,
    tickets_collection,
    users_collection,
)
from app.services.support import save_appointment
from app.services.notifications import (
    _notify_admin_appointment_scheduled,
    _notify_staff_appointment_scheduled,
    _create_appointment_notification
)

router = APIRouter()


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

    # Handle staff assignment
    if assigned_staff == "auto-assign-admin":
        admin_user = users_collection.find_one({"role": "admin"})
        if admin_user:
            assigned_staff = admin_user.get("email")
            assigned_staff_name = admin_user.get(
                "full_name", admin_user.get("email"))
        else:
            return JSONResponse({"success": False, "error": "Admin user not found"}, status_code=500)
    else:
        staff_member = users_collection.find_one({"email": assigned_staff})
        assigned_staff_name = staff_member.get(
            "full_name") if staff_member else assigned_staff

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
        """ await _create_appointment_notification(appt, str(inserted_id)) """
        return {"success": True, "appointment_id": str(inserted_id)}
    except Exception as exc:
        print(f"[ERROR] /book_appointment exception: {exc}")
        return JSONResponse({"success": False, "error": str(exc)}, status_code=500)


@router.post("/api/appointments/cancel/{appointment_id}")
async def cancel_appointment(appointment_id: str):
    try:
        result = appointments_collection.update_one(
            {"_id": ObjectId(appointment_id)}, {
                "$set": {"status": "Cancelled"}}
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
            {"$set": {"date": new_date, "time_slot": new_time,
                      "last_updated": datetime.utcnow().isoformat()}},
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Appointment rescheduled."}
        return {"success": False, "message": "Appointment not found or not updated."}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------
# 
#  [DELETED] The first, simpler "@router.get('/api/appointments')"
#            function was removed from here.
# 
# ------------------------------------------------------------------


@router.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str):
    try:
        appt = appointments_collection.find_one(
            {"_id": ObjectId(appointment_id)})
        if not appt:
            raise HTTPException(
                status_code=404, detail="Appointment not found")
        appt["_id"] = str(appt["_id"])
        return {"success": True, "appointment": appt}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.put("/api/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, request: Request):
    try:
        appointment = appointments_collection.find_one(
            {"_id": ObjectId(appointment_id)})
        if not appointment:
            raise HTTPException(
                status_code=404, detail="Appointment not found")

        body = await request.form()
        update_fields = {}
        for key in ["department", "subject", "date", "time_slot", "meeting_mode", "notes", "assigned_staff"]:
            if key in body:
                update_fields[key] = body.get(key)

        if update_fields:
            update_fields["last_updated"] = datetime.utcnow().isoformat()
            result = appointments_collection.update_one(
                {"_id": ObjectId(appointment_id)}, {"$set": update_fields})
            if result.modified_count == 0:
                return JSONResponse({"success": False, "message": "No changes applied."}, status_code=200)

        return {"success": True, "message": "Appointment updated."}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


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
            raise HTTPException(
                status_code=404, detail="Appointment not found")
        return {"success": True, "message": "Appointment confirmed"}
    except HTTPException:
        raise
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/appointments")
async def api_appointments(  # This is the one we are keeping
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