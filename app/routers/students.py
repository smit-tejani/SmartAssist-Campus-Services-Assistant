from __future__ import annotations
import os
import re
import fitz
from datetime import datetime

from typing import Any, Dict, List
from fastapi import UploadFile, File, HTTPException, Request, Form, APIRouter
from bson import ObjectId
from pydantic import BaseModel, ValidationError

from app.db.mongo import registrations_collection, students_collection, courses_collection, db
from app.core.config import UPLOAD_DIR

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
    registrations = list(registrations_collection.find(
        {"student_email": student_email}))
    registered_courses: List[Dict[str, Any]] = []

    for registration in registrations:
        course = courses_collection.find_one(
            {"_id": ObjectId(registration["course_id"])})
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
    update_fields = {k: v for k, v in student_data.dict().items()
                     if v is not None}
    if not update_fields:
        return {"message": "No fields to update"}

    result = students_collection.update_one(
        {"email": email}, {"$set": update_fields})
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

    registrations = list(
        registrations_collection.find({"student_email": email}))
    registered_classes = []

    for registration in registrations:
        course = courses_collection.find_one(
            {"_id": ObjectId(registration["course_id"])})
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

# ------------------------------------------------------------------------------
# Profile Picture Upload
# ------------------------------------------------------------------------------
from fastapi import UploadFile, File
from pathlib import Path


@router.post("/api/student/{email}/profile_picture")
async def upload_profile_picture(email: str, file: UploadFile = File(...)):
    """
    Upload a profile picture for the specified student.

    The file is saved into a subdirectory of the static folder and the
    student's record is updated with the relative file path.  Only image
    formats (png, jpg, jpeg, gif) are accepted.
    """
    # Validate file extension
    filename = file.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in {"png", "jpg", "jpeg", "gif"}:
        raise HTTPException(status_code=400, detail="Unsupported file type. Please upload an image.")

    # Ensure upload directory exists
    upload_dir = Path("static/uploads/profile_pictures")
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Compose unique file name
    timestamp = int(datetime.utcnow().timestamp())
    safe_email = email.replace("@", "_").replace(".", "_")
    saved_name = f"{safe_email}_{timestamp}.{ext}"
    file_path = upload_dir / saved_name

    # Save file content
    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # Update student record with new profile image path
    result = students_collection.update_one(
        {"email": email},
        {"$set": {"profile_image": f"/static/uploads/profile_pictures/{saved_name}"}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Student not found")

    return {"success": True, "url": f"/static/uploads/profile_pictures/{saved_name}"}


@router.get("/api/materials/mine")
def get_my_materials(request: Request):
    user = request.session.get("user")
    if not user or user.get("role") != "student":
        raise HTTPException(403, "Not allowed")

    email = user["email"]

    # 1) get student registrations
    regs = list(db.registrations.find({"student_email": email}))
    course_ids = [ObjectId(r["course_id"]) for r in regs]

    if not course_ids:
        return []

    # 2) get materials for these courses
    mats = list(db.course_materials.find({
        "course_id": {"$in": course_ids},
        "visible": True
    }).sort("uploaded_at", -1))

    result = []
    for m in mats:
        # compute file_url for client if a local file is present
        file_url = None
        if m.get("file_name"):
            file_url = f"/static/uploads/materials/{m['file_name']}"
        result.append({
            "_id": str(m["_id"]),
            "course_id": str(m["course_id"]),
            "course_title": m.get("course_title"),
            "title": m.get("title"),
            "description": m.get("description"),
            "file_name": m.get("file_name"),
            "file_url": file_url,
            "external_url": m.get("external_url"),
            "uploaded_by": m.get("uploaded_by"),
            "uploaded_at": m.get("uploaded_at").isoformat() if m.get("uploaded_at") else None,
        })
    return result


@router.get("/api/debug/courses")
def debug_courses(request: Request):
    user = request.session.get("user")
    # get all courses the backend is ACTUALLY seeing
    courses = list(db.courses.find({}))
    # keep it light
    preview = []
    for c in courses[:10]:
        preview.append({
            "_id": str(c["_id"]),
            "title": c.get("title"),
            "details": c.get("details"),
            "term": c.get("term"),
            "staff_emails": c.get("staff_emails"),
        })
    return {
        "session_user": user,
        "courses_count": len(courses),
        "courses_preview": preview,
    }


@router.post("/api/materials")
async def create_course_material(
    request: Request,
    course_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    file: UploadFile | None = File(None),
    external_url: str = Form(""),
):
    user = request.session.get("user")
    if not user or user.get("role") not in ("staff", "admin"):
        raise HTTPException(403, "Not allowed")

    course = db.courses.find_one({"_id": ObjectId(course_id)})
    if not course:
        raise HTTPException(404, "Course not found")

    saved_filename = None
    saved_path = None
    file_type = None

    # -------------------------------------------------------------------
    # Save uploaded file into a static directory for public access.
    # We sanitize the filename to prevent directory traversal and ensure
    # uniqueness. Files are stored under static/uploads/materials.
    # Previously the code attempted to save files into both `UPLOAD_DIR`
    # (uploads) and the relative `uploads/` folder, which is not served
    # by FastAPI and resulted in 404 links. Here we write only once to a
    # static subdirectory so the file is accessible at `/static/uploads/materials/<name>`.
    # -------------------------------------------------------------------
    if file and file.filename:
        # sanitize filename
        original_name = file.filename
        cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", original_name)
        # ensure subdir exists
        from pathlib import Path
        mat_dir = Path("static/uploads/materials")
        mat_dir.mkdir(parents=True, exist_ok=True)
        # ensure unique filename with timestamp
        timestamp = int(datetime.utcnow().timestamp())
        name_parts = cleaned.rsplit(".", 1)
        if len(name_parts) == 2:
            base, ext = name_parts
            cleaned = f"{base}_{timestamp}.{ext}"
        else:
            cleaned = f"{cleaned}_{timestamp}"
        abs_path = mat_dir / cleaned
        # save content
        contents = await file.read()
        with open(abs_path, "wb") as f:
            f.write(contents)
        saved_filename = cleaned
        saved_path = f"/static/uploads/materials/{cleaned}"
        file_type = file.content_type or "application/octet-stream"
    elif external_url:
        # if an external URL is provided, we don't save a file but record the link
        saved_path = external_url
        file_type = "link"

    doc = {
        "course_id": course["_id"],
        "course_title": course.get("title"),
        "title": title,
        "description": description,
        "uploaded_by": user["email"],
        "uploaded_at": datetime.utcnow(),
        "visible": True,
    }

    # store file metadata
    if saved_filename:
        # use sanitized filename and compute URL relative to static so front-end can build link
        doc["file_name"] = saved_filename
        # also include file_url for convenience
        doc["file_url"] = saved_path  # e.g. /static/uploads/materials/<name>

    # store external URL if provided
    if external_url:
        doc["external_url"] = external_url

    result = db.course_materials.insert_one(doc)
    material_id = result.inserted_id
    # try to extract text if it's a PDF; use the saved absolute path computed above
    try:
        if saved_filename and saved_filename.lower().endswith(".pdf"):
            # compute absolute path for the saved file under static/uploads/materials
            abs_path_for_pdf = os.path.join("static/uploads/materials", saved_filename)
            text = extract_pdf_text(abs_path_for_pdf)  # we'll define this below
            if text:
                db.course_materials_text.insert_one({
                    "material_id": material_id,
                    "course_id": doc["course_id"],
                    "course_title": doc.get("course_title"),
                    "file_name": saved_filename,
                    "text": text,
                })
    except Exception as e:
        print("[WARN] could not extract text from material:", e)


# get materials for a course
@router.get("/api/materials/by_course/{course_id}")
async def get_materials_by_course(course_id: str):
    mats = list(db.course_materials.find({
        "course_id": ObjectId(course_id),
        "visible": True
    }).sort("uploaded_at", -1))
    # convert ObjectId -> str
    out: list[dict] = []
    for m in mats:
        # convert IDs
        m_id = str(m["_id"])
        c_id = str(m["course_id"])
        # compute file_url for local files
        file_url = None
        if m.get("file_name"):
            file_url = f"/static/uploads/materials/{m['file_name']}"
        out.append({
            "_id": m_id,
            "course_id": c_id,
            "course_title": m.get("course_title"),
            "title": m.get("title"),
            "description": m.get("description"),
            "file_name": m.get("file_name"),
            "file_url": file_url,
            "external_url": m.get("external_url"),
            "uploaded_by": m.get("uploaded_by"),
            "uploaded_at": m.get("uploaded_at").isoformat() if m.get("uploaded_at") else None,
            "visible": m.get("visible", True),
        })
    return out


def extract_pdf_text(path: str) -> str:
    doc = fitz.open(path)
    parts = []
    for page in doc:
        parts.append(page.get_text())
    return "\n".join(parts)


@router.get("/api/materials/all")
async def get_all_materials(request: Request):
    user = request.session.get("user")
    if not user or user.get("role") not in ("staff", "admin"):
        raise HTTPException(403, "Not allowed")

    mats = list(db.course_materials.find({}).sort("uploaded_at", -1))
    # get titles for each course
    course_ids = list({m["course_id"] for m in mats})
    courses = {c["_id"]: c for c in db.courses.find(
        {"_id": {"$in": course_ids}})}

    out: list[dict] = []
    for m in mats:
        cid = m["course_id"]
        course = courses.get(cid)
        # compute file_url for local files
        file_url = None
        if m.get("file_name"):
            file_url = f"/static/uploads/materials/{m['file_name']}"
        out.append({
            "_id": str(m["_id"]),
            "course_id": str(cid),
            "course_title": course["title"] if course else "Unknown course",
            "title": m.get("title"),
            "description": m.get("description"),
            "file_name": m.get("file_name"),
            "file_url": file_url,
            "external_url": m.get("external_url"),
            "uploaded_by": m.get("uploaded_by"),
            "uploaded_at": m.get("uploaded_at").isoformat() if m.get("uploaded_at") else None,
            "visible": m.get("visible", True),
        })
    return out