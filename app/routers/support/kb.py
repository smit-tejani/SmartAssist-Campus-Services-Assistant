# app/routers/support/kb.py
import io
from datetime import date
from bson import ObjectId
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.db.mongo import kb_collection, db, fs, appointments_collection, users_collection, tickets_collection

router = APIRouter()

# ---------------------------------------------------------------------
# Debug endpoint (includes KB stats)
# ---------------------------------------------------------------------


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


# ---------------------------------------------------------------------
# File attachment download
# ---------------------------------------------------------------------
@router.get("/api/attachment/{file_id}")
async def api_attachment(file_id: str):
    try:
        grid_out = fs.get(ObjectId(file_id))
        data = grid_out.read()
        return StreamingResponse(
            io.BytesIO(data),
            media_type=(grid_out.content_type or "application/octet-stream"),
            headers={
                "Content-Disposition": f'attachment; filename="{grid_out.filename or file_id}"'
            },
        )
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)


# ---------------------------------------------------------------------
# System statistics (includes KB)
# ---------------------------------------------------------------------
@router.get("/api/stats")
async def get_stats():
    try:
        knowledge_articles_count = kb_collection.count_documents({})
        departments_count = db.departments.count_documents(
            {"status": "active"})
        total_users_count = users_collection.count_documents({})
        upcoming_appointments_count = appointments_collection.count_documents(
            {"status": {"$ne": "Cancelled"}, "date": {
                "$gte": date.today().isoformat()}}
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


# ---------------------------------------------------------------------
# Get all knowledge base articles
# ---------------------------------------------------------------------
@router.get("/api/knowledge_base")
async def get_knowledge_base():
    try:
        articles = list(kb_collection.find({}, {"_id": 0}))
        return {"articles": articles}
    except Exception as exc:
        print(f"Error fetching knowledge base articles: {exc}")
        return {"articles": []}


# ---------------------------------------------------------------------
# Add a new knowledge base article (fetch from URL)
# ---------------------------------------------------------------------
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
            return JSONResponse(
                {"error": "Failed to fetch content from URL."}, status_code=400
            )

        save_to_db(article)
        return JSONResponse(
            {"message": "Article added successfully."}, status_code=201
        )
    except Exception as exc:
        print(f"Error adding article: {exc}")
        return JSONResponse(
            {"error": "Internal server error."}, status_code=500
        )
