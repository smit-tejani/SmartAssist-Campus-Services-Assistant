from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.db.mongo import users_collection

router = APIRouter()


@router.get("/api/staff")
def get_all_staff():
    try:
        staff_members = list(users_collection.find({"role": "staff", "status": "active"}, {"password": 0}))
        for staff in staff_members:
            staff["_id"] = str(staff["_id"])
        return staff_members
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/staff/department/{department}")
def get_staff_by_department(department: str):
    try:
        staff_members = list(
            users_collection.find(
                {"role": "staff", "department": department, "status": "active"},
                {"password": 0},
            )
        )
        for staff in staff_members:
            staff["_id"] = str(staff["_id"])
        return staff_members
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
