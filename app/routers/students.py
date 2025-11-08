from __future__ import annotations

from typing import Any, Dict, List

from bson import ObjectId
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError

from app.db.mongo import registrations_collection, students_collection, courses_collection

router = APIRouter()


def convert_objectid_to_str(doc):
    if isinstance(doc, list):
        return [convert_objectid_to_str(d) for d in doc]
    if isinstance(doc, dict):
        return {k: convert_objectid_to_str(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    return doc


@router.get("/api/courses/{term}")
def get_courses(term: str):
    courses = list(courses_collection.find({"term": term}))
    return convert_objectid_to_str(courses)


class CourseRegistration(BaseModel):
    student_email: str
    course_id: str
    term: str


@router.post("/api/register_course")
def register_course(registration: CourseRegistration):
    try:
        registration_data = registration.dict()
        registrations_collection.insert_one(registration_data)
        return {"message": "Registration successful"}
    except ValidationError as exc:
        return {"error": "Invalid registration data", "details": exc.errors()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/registered_courses/{student_email}")
def get_registered_courses(student_email: str):
    registrations = list(registrations_collection.find({"student_email": student_email}))
    registered_courses: List[Dict[str, Any]] = []

    for registration in registrations:
        course = courses_collection.find_one({"_id": ObjectId(registration["course_id"])})
        if course:
            course["_id"] = str(course["_id"])
            registration["course_details"] = course
        registration["_id"] = str(registration["_id"])
        registered_courses.append(registration)

    return registered_courses


@router.get("/api/student/{email}")
def get_student(email: str):
    student = students_collection.find_one({"email": email})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student["_id"] = str(student["_id"])
    return student


class StudentUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    major: str | None = None
    year: str | None = None
    address: str | None = None
    emergency_contact: Dict[str, Any] | None = None


@router.put("/api/student/{email}")
def update_student(email: str, student_data: StudentUpdate):
    update_fields = {k: v for k, v in student_data.dict().items() if v is not None}
    if not update_fields:
        return {"message": "No fields to update"}

    result = students_collection.update_one({"email": email}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")

    updated_student = students_collection.find_one({"email": email})
    updated_student["_id"] = str(updated_student["_id"])
    return updated_student


@router.get("/api/student/{email}/registered_classes")
def get_registered_classes(email: str):
    student = students_collection.find_one({"email": email})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    registrations = list(registrations_collection.find({"student_email": email}))
    registered_classes = []

    for registration in registrations:
        course = courses_collection.find_one({"_id": ObjectId(registration["course_id"])})
        if course:
            course["_id"] = str(course["_id"])
            registration["course_details"] = course
        registration["_id"] = str(registration["_id"])
        registered_classes.append(registration)

    return registered_classes


@router.get("/api/students")
def get_all_students():
    students = list(students_collection.find({}, {"password": 0}))
    for student in students:
        student["_id"] = str(student["_id"])
    return students
