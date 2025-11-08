from __future__ import annotations

from datetime import datetime

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request

from app.db.mongo import departments_collection

router = APIRouter()


@router.get("/api/departments")
async def get_departments(status: str | None = None):
    query = {}
    if status:
        query["status"] = status
    departments = list(departments_collection.find(query))
    for dept in departments:
        dept["_id"] = str(dept["_id"])
    return departments


@router.get("/api/departments/{department_id}")
async def get_department(department_id: str):
    department = departments_collection.find_one({"_id": ObjectId(department_id)})
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    department["_id"] = str(department["_id"])
    return department


@router.post("/api/departments")
async def create_department(request: Request):
    data = await request.json()
    if not data.get("name"):
        raise HTTPException(status_code=400, detail="Department name is required")

    department = {
        "name": data["name"],
        "description": data.get("description", ""),
        "status": data.get("status", "active"),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }
    result = departments_collection.insert_one(department)
    department["_id"] = str(result.inserted_id)
    return department


@router.put("/api/departments/{department_id}")
async def update_department(department_id: str, request: Request):
    data = await request.json()
    updates = {"updated_at": datetime.utcnow().isoformat()}
    for field in ["name", "description", "status"]:
        if field in data:
            updates[field] = data[field]

    result = departments_collection.update_one({"_id": ObjectId(department_id)}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")

    department = departments_collection.find_one({"_id": ObjectId(department_id)})
    department["_id"] = str(department["_id"])
    return department


@router.delete("/api/departments/{department_id}")
async def delete_department(department_id: str):
    result = departments_collection.delete_one({"_id": ObjectId(department_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Department not found")
    return {"message": "Department deleted"}
