from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, File, UploadFile, Depends, HTTPException, Body
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Dict
from pymongo import MongoClient
import gridfs
import os
import json
from pathlib import Path
from fastapi.responses import JSONResponse
from datetime import date, datetime, timedelta
from bson import ObjectId
import io
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, ValidationError
import logging

# ---------- env ----------
load_dotenv()
print("[BOOT] USE_LLM_FOLLOWUPS=", os.getenv("USE_LLM_FOLLOWUPS", "1"),
      "MODEL=", os.getenv("FOLLOWUP_MODEL", "gpt-4o-mini"),
      "OPENAI_KEY_PRESENT=", bool(os.getenv("OPENAI_API_KEY")))

# ------------------ FastAPI App ------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")  # folder for HTML templates

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "your-secret-key-change-in-production-12345"),
    same_site="lax",
    https_only=False,
    max_age=3600,  # Session expires after 1 hour
    session_cookie="session"  # Explicit cookie name
)

# Configure OAuth for Google
oauth = OAuth()
oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

# ------------------ MongoDB Setup ------------------
MONGO_URI = os.getenv("MONGODB_URI", "mongodb://mongo:27017/smartassist")
client = MongoClient(MONGO_URI)
db = client.smartassist
users_collection = db.users
live_chat_collection = db.live_chat
live_chat_sessions = db.live_chat_sessions
kb_collection        = db.knowledge_base   # <-- use the collection from your screenshot
fs = gridfs.GridFS(db)

# Ensure a text index exists for follow-ups (safe to call once)
try:
    kb_collection.create_index(
        [("title", "text"), ("content", "text"), ("category", "text")]
    )
except Exception:
    pass

# ======================================================================================
#                                    OpenAI SHIM
# Works with both OpenAI SDK v1 (OpenAI()) and legacy v0 (openai.ChatCompletion.create)
# ======================================================================================
def llm_complete(messages, model="gpt-4o-mini", temperature=0.4, max_tokens=180) -> str:
    """
    Returns assistant text using whichever OpenAI SDK is installed.
    Uses a legacy-safe model when v0 SDK is detected.
    """
    # Try v1 SDK
    try:
        from openai import OpenAI  # v1+
        client = OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            # if your model supports it, uncomment next line to force JSON
            # response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content.strip()
    except Exception as v1_err:
        # Fallback to legacy v0 SDK
        import openai
        if not getattr(openai, "api_key", None):
            openai.api_key = os.getenv("OPENAI_API_KEY")
        legacy_model = os.getenv("FOLLOWUP_MODEL_LEGACY", "gpt-3.5-turbo")
        try:
            resp = openai.ChatCompletion.create(
                model=legacy_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as v0_err:
            # bubble up a unified error so caller can switch to fallback
            raise RuntimeError(f"OpenAI failed (v1: {v1_err!r}; v0: {v0_err!r})")


# ======================================================================================
#                        LLM-STYLE, INTENT-LIKE FOLLOW-UPS
# ======================================================================================
USE_LLM_FOLLOWUPS = os.getenv("USE_LLM_FOLLOWUPS", "1") == "1"
FOLLOWUP_MODEL    = os.getenv("FOLLOWUP_MODEL", "gpt-4o-mini")
DEBUG_FOLLOWUPS   = os.getenv("DEBUG_FOLLOWUPS", "1") == "1"

ESCALATION_KEYWORDS = {
    "agent","human","person","representative","talk to someone","talk to admin",
    "live chat","connect me","escalate","call","phone","help desk","support"
}

def _wants_human(text: str) -> bool:
    q = (text or "").lower()
    return any(k in q for k in ESCALATION_KEYWORDS)

def _mongo_text_search(query: str, limit: int = 8) -> List[Dict]:
    if not (query and query.strip()):
        return []
    cur = (
        kb_collection.find(
            {"$text": {"$search": query}},
            {
                "title": 1,
                "category": 1,
                "url": 1,
                "score": {"$meta": "textScore"},
            },
        )
        .sort([("score", {"$meta": "textScore"})])
        .limit(limit)
    )
    return list(cur)


def _should_offer_live_chat(user_q: str, answer_text: str, hits: int) -> bool:
    # If they explicitly ask for a human, always escalate
    if _wants_human(user_q):
        return True

    low_conf = [
        "i'm not sure",
        "no information",
        "could not find",
        "not available",
        "i don't have",
        "unable to find",
    ]
    a = (answer_text or "").lower()

    # Only escalate when the answer itself looks uncertain
    return any(p in a for p in low_conf)
    # (optional stricter version if you want: return hits == 0 and any(p in a for p in low_conf))


import re

def _safe_json_list(s: str) -> List[str]:
    """
    Be forgiving: extract the first JSON array from the text and parse it.
    Returns [] if nothing usable is found.
    """
    if not s:
        return []
    # already a pure array?
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, (str,))]
    except Exception:
        pass

    # try to find a [...] substring
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        frag = m.group(0)
        try:
            data = json.loads(frag)
            if isinstance(data, list):
                return [str(x) for x in data if isinstance(x, (str,))]
        except Exception:
            return []
    return []


def _llm_generate_followups(user_q: str, answer_text: str, candidates: List[Dict], k: int = 4) -> List[str]:
    # pack candidate KB as lightweight grounding
    ctx_lines = []
    for c in candidates[:10]:
        t = (c.get("title") or "").strip()
        u = c.get("url","")
        cat = c.get("category","")
        if t:
            ctx_lines.append(f"- {t} [{cat}] {u}")
    ctx = "\n".join(ctx_lines) if ctx_lines else "(no candidates)"

    sys = (
        "You are a campus assistant crafting follow-up suggestions that feel like the student's next question. "
        "Return ONLY a JSON array of 3-5 strings. Constraints: "
        "1) Each suggestion must be a natural, concise user question (max ~10 words). "
        "2) Avoid vague labels and categories; be specific. "
        "3) No duplicates, no punctuation at the end, no numbering. "
        "4) Suggestions must be answerable by the provided knowledge items when possible."
    )
    usr = (
        f"Student question: {user_q}\n\n"
        f"Assistant answer:\n{answer_text}\n\n"
        f"Relevant knowledge items:\n{ctx}\n\n"
        "Produce a JSON array of short follow-up questions likely to be asked next."
    )

    text = llm_complete(
        messages=[{"role":"system","content":sys}, {"role":"user","content":usr}],
        model=FOLLOWUP_MODEL,
        temperature=0.4,
        max_tokens=180,
    )
    items = _safe_json_list(text)
    uniq, seen = [], set()
    for it in items:
        s = it.strip()
        if s.endswith("?"): s = s[:-1]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
        if len(uniq) >= k:
            break
    return uniq

def build_llm_style_followups(user_question: str, answer_text: str, k: int = 4):
    """
    Returns (chips, suggest_live_chat_flag, source).
    source ∈ {"openai","fallback","fallback_error"} to help you verify.
    """
    hits = _mongo_text_search(user_question, limit=8)
    if not hits and answer_text:
        hits = _mongo_text_search(answer_text, limit=8)

    suggestions: List[str] = []
    source = "fallback"

    if USE_LLM_FOLLOWUPS and os.getenv("OPENAI_API_KEY"):
        try:
            suggestions = _llm_generate_followups(user_question, answer_text, hits, k=k)
            if suggestions:
                source = "openai"
        except Exception as e:
            print("[LLM] followups error:", repr(e))
            suggestions = []
            source = "fallback_error"

    if not suggestions:
        # graceful fallback: try KB titles, then a tiny curated list
        base = [h.get("title","") for h in hits[:6] if h.get("title")]
        suggestions = [s for s in base if s][:k]
        if not suggestions:
            suggestions = [
                "application deadlines for scholarships",
                "GPA needed for freshman admission",
                "who is my admissions counselor",
                "how to apply for scholarships",
            ][:k]

    chips = [{"label": s, "payload": {"type": "faq", "query": s}} for s in suggestions]
    suggest_live_chat = _should_offer_live_chat(user_question, answer_text, hits=len(hits))
    return chips[:k], suggest_live_chat, source


# ======================================================================================
#                               LLM DIAGNOSTIC ENDPOINT
# ======================================================================================
@app.get("/diag/llm")
def diag_llm():
    try:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY missing")
        text = llm_complete(
            messages=[{"role":"system","content":"Return the word OK"}],
            model=FOLLOWUP_MODEL,
            temperature=0.0,
            max_tokens=4,
        )
        return {"ok": True, "model": FOLLOWUP_MODEL, "reply": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}

print("[BOOT]",
      "USE_LLM_FOLLOWUPS=", USE_LLM_FOLLOWUPS,
      "MODEL=", FOLLOWUP_MODEL,
      "OPENAI_KEY_PRESENT=", bool(os.getenv("OPENAI_API_KEY")))

# ======================================================================================
#                                 LIVE CHAT MANAGER
# ======================================================================================
import anyio

class ChatManager:
    def __init__(self):
        self.admins: List[WebSocket] = []
        self.students: dict = {}  # session_id -> WebSocket

    async def connect_admin(self, websocket: WebSocket):
        await websocket.accept()
        self.admins.append(websocket)
        print("✅ Admin connected")

    async def connect_student(self, websocket: WebSocket, session_id: str):
        await websocket.accept()
        self.students[session_id] = websocket
        print(f"✅ Student connected: {session_id}")

        # ensure record; DO NOT broadcast yet (only /escalate does)
        live_chat_sessions.update_one(
            {"session_id": session_id},
            {
                "$setOnInsert": {
                    "session_id": session_id,
                    "status": "queued",
                    "assigned_admin": None,
                    "name": f"Student {session_id[:4]}"
                },
                "$set": {"student_connected": True}
            },
            upsert=True
        )

        if not live_chat_collection.find_one({"session_id": session_id}):
            live_chat_collection.insert_one({
                "session_id": session_id,
                "sender": "system",
                "message": "New chat session started.",
                "created_at": datetime.utcnow()
            })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.admins:
            self.admins.remove(websocket)
            print("❌ Admin disconnected")
        else:
            for sid, ws in list(self.students.items()):
                if ws == websocket:
                    del self.students[sid]
                    live_chat_sessions.update_one(
                        {"session_id": sid},
                        {"$set": {"student_connected": False, "status": "closed"}}
                    )
                    # remove from admin UI immediately
                    anyio.from_thread.run(self.broadcast_admins, {"type": "session_removed", "session_id": sid})
                    print(f"❌ Student disconnected: {sid}")

    async def send_to_student(self, session_id: str, message: dict):
        if session_id in self.students:
            await self.students[session_id].send_json(message)

    async def broadcast_admins(self, message: dict):
        for admin in list(self.admins):
            try:
                await admin.send_json(message)
            except Exception:
                # stale socket
                pass

manager = ChatManager()



def save_ticket(ticket: dict, attachment: UploadFile | None = None):
    # store ticket in MongoDB; if attachment provided, save in GridFS and reference file id
    if attachment is not None:
        try:
            content = attachment.file.read()
            file_id = fs.put(content, filename=attachment.filename, contentType=attachment.content_type)
            ticket["attachment_id"] = file_id
            ticket["attachment_name"] = attachment.filename
            ticket["attachment_content_type"] = attachment.content_type
        except Exception as e:
            ticket["attachment_error"] = f"failed to save to gridfs: {e}"

    result = db.tickets.insert_one(ticket)
    inserted_id = result.inserted_id

    # Debug output: print inserted id and ticket document (attachment_id as string)
    debug_doc = ticket.copy()
    if "attachment_id" in debug_doc:
        debug_doc["attachment_id"] = str(debug_doc["attachment_id"])
    try:
        print(f"[DEBUG] Inserted ticket id: {inserted_id}")
        print(f"[DEBUG] Ticket document: {json.dumps(debug_doc, default=str)}")
    except Exception:
        # fallback simple print if json.dumps fails
        print("[DEBUG] Ticket document (fallback):", debug_doc)

    return inserted_id

@app.post("/raise_ticket")
async def raise_ticket(
    subject: str = Form(...),
    category: str = Form(...),
    priority: str = Form(...),
    description: str = Form(...),
    student_email: str = Form(...),
    student_name: str = Form(...),
    preferred_staff: str = Form(""),
    attachment: UploadFile | None = File(None)
):
    # Basic validation
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
        "assigned_to_name": None
    }
    
    # Handle auto-assign to admin for Administrative category
    if preferred_staff == "auto-assign-admin":
        admin_user = db.users.find_one({"role": "admin"})
        if admin_user:
            ticket["assigned_staff"] = admin_user.get("email")
            ticket["assigned_to_name"] = admin_user.get("full_name", admin_user.get("email"))
            ticket["assigned_at"] = datetime.now().isoformat()
            ticket["preferred_staff"] = None
            ticket["preferred_staff_name"] = None
        else:
            # Fallback if no admin found
            ticket["preferred_staff"] = None
            ticket["preferred_staff_name"] = None
    # Add preferred staff if provided
    elif preferred_staff:
        staff_member = db.users.find_one({"email": preferred_staff})
        if staff_member:
            ticket["preferred_staff"] = preferred_staff
            ticket["preferred_staff_name"] = staff_member.get("full_name", preferred_staff)
        else:
            ticket["preferred_staff"] = preferred_staff
            ticket["preferred_staff_name"] = preferred_staff

    try:
        inserted_id = save_ticket(ticket, attachment)
        print(f"[DEBUG] /raise_ticket: inserted_id={inserted_id} attachment_present={attachment is not None}")
        
        # Notify admins about new ticket
        await _notify_admin_new_ticket(ticket, str(inserted_id))
        
        return {"success": True, "ticket_id": str(inserted_id)}
    except Exception as e:
        print(f"[ERROR] /raise_ticket exception: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

def save_appointment(appt: dict, attachment: UploadFile | None = None):
    # store appointment in MongoDB; if attachment provided, save in GridFS and reference file id
    if attachment is not None:
        try:
            content = attachment.file.read()
            file_id = fs.put(content, filename=attachment.filename, contentType=attachment.content_type)
            appt["attachment_id"] = file_id
            appt["attachment_name"] = attachment.filename
            appt["attachment_content_type"] = attachment.content_type
        except Exception as e:
            appt["attachment_error"] = f"failed to save to gridfs: {e}"

    result = db.appointments.insert_one(appt)
    inserted_id = result.inserted_id

    # Debug output: print inserted id and appointment document (attachment_id as string)
    debug_doc = appt.copy()
    if "attachment_id" in debug_doc:
        debug_doc["attachment_id"] = str(debug_doc["attachment_id"])
    try:
        print(f"[DEBUG] Inserted appointment id: {inserted_id}")
        print(f"[DEBUG] Appointment document: {json.dumps(debug_doc, default=str)}")
    except Exception:
        print("[DEBUG] Appointment document (fallback):", debug_doc)

    return inserted_id


# ======================================================================================
#                                   WEBSOCKETS
# ======================================================================================
@app.websocket("/ws/student/{session_id}")
async def student_ws(websocket: WebSocket, session_id: str):
    print(f"[DEBUG] Student connected with session_id: {session_id}")
    await manager.connect_student(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_json()
            message_text = data.get("message", "")
            print(f"[DEBUG] Received message from student: {message_text}")

            live_chat_collection.insert_one({
                "session_id": session_id,
                "sender": "student",
                "message": message_text,
                "created_at": datetime.utcnow()
            })

            sess = live_chat_sessions.find_one({"session_id": session_id})
            if sess and sess.get("status") == "live":
                await manager.broadcast_admins({
                    "type": "message",
                    "session_id": session_id,
                    "sender": "student",
                    "message": message_text
                })
            else:
                # Calculate queue position
                queued_sessions = list(live_chat_sessions.find({"status": "queued"}).sort("created_at", 1))
                queue_position = next((i + 1 for i, s in enumerate(queued_sessions) if s["session_id"] == session_id), None)

                await manager.broadcast_admins({
                    "type": "queued_ping",
                    "session_id": session_id,
                    "queue_position": queue_position
                })

    except WebSocketDisconnect:
        print(f"[DEBUG] Student disconnected with session_id: {session_id}")
        manager.disconnect(websocket)

@app.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket):
    print("[DEBUG] /ws/admin endpoint accessed")
    print("[DEBUG] Admin connected")
    await manager.connect_admin(websocket)
    admin_id = str(id(websocket))

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            print(f"[DEBUG] Received message from admin: {data}")

            if msg_type == "join":
                session_id = data.get("session_id")
                sess = live_chat_sessions.find_one({"session_id": session_id})
                if not sess or not sess.get("student_connected") or sess.get("status") == "closed":
                    await websocket.send_json({"type": "error", "reason": "Student not connected / session closed."})
                    await websocket.send_json({"type": "session_removed", "session_id": session_id})
                    continue

                res = live_chat_sessions.update_one(
                    {"session_id": session_id, "status": {"$in": ["queued","live"]}},
                    {"$set": {"status": "live", "assigned_admin": admin_id}}
                )
                if res.matched_count == 0:
                    await websocket.send_json({"type": "error", "reason": "Session not found or closed."})
                    continue

                # Get updated session to include student info in response
                sess = live_chat_sessions.find_one({"session_id": session_id})

                await manager.send_to_student(session_id, {
                    "type": "status",
                    "session_id": session_id,
                    "status": "live"
                })
                await websocket.send_json({
                    "type": "joined", 
                    "session_id": session_id,
                    "student_name": sess.get("student_name"),
                    "student_email": sess.get("student_email")
                })

            elif msg_type == "message":
                session_id = data.get("session_id")
                message_text = data.get("message", "")

                sess = live_chat_sessions.find_one({"session_id": session_id})
                if not sess or sess.get("status") != "live" or sess.get("assigned_admin") != admin_id:
                    await websocket.send_json({"type": "error", "reason": "Session not live or not assigned to you."})
                    continue

                live_chat_collection.insert_one({
                    "session_id": session_id,
                    "sender": "admin",
                    "message": message_text,
                    "created_at": datetime.utcnow()
                })
                await manager.send_to_student(session_id, {
                    "type": "message",
                    "session_id": session_id,
                    "sender": "admin",
                    "message": message_text
                })

            else:
                await websocket.send_json({"type":"error","reason":"Unknown message type."})

    except WebSocketDisconnect:
        print("[DEBUG] Admin disconnected")
        manager.disconnect(websocket)

# ======================================================================================
#                                   REST API
# ======================================================================================
@app.get("/api/chat/{session_id}")
async def get_chat_history(session_id: str):
    print(f"[DEBUG] Fetching chat history for session_id: {session_id}")
    messages = list(live_chat_collection
                    .find({"session_id": session_id}, {"_id": 0})
                    .sort("created_at", 1))
    print(f"[DEBUG] Retrieved messages: {messages}")
    return messages

@app.post("/api/chat/{session_id}/escalate")
async def escalate(session_id: str, student_info: dict = Body(default={})):
    # student has asked for an agent — surface to admins now
    # Extract student information from request body
    student_name = student_info.get("student_name", f"Student {session_id[:4]}")
    student_email = student_info.get("student_email", None)
    
    live_chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$setOnInsert": {
                "session_id": session_id,
                "assigned_admin": None,
                "created_at": datetime.utcnow()
            },
            "$set": {
                "status": "queued",
                "student_connected": True,
                "student_name": student_name,
                "student_email": student_email,
                "name": student_name  # For backward compatibility
            },
        },
        upsert=True
    )
    await manager.broadcast_admins({
        "type": "new_session",
        "session_id": session_id,
        "status": "queued",
        "student_name": student_name,
        "student_email": student_email,
        "name": student_name
    })
    return {"ok": True}

@app.post("/api/chat/{session_id}/end")
async def end_chat(session_id: str):
    live_chat_sessions.update_one(
        {"session_id": session_id},
        {"$set": {
            "status": "closed",
            "student_connected": False,
            "assigned_admin": None,
            "ended_at": datetime.utcnow()
        }}
    )
    await manager.broadcast_admins({"type": "session_removed", "session_id": session_id})
    return {"ok": True}

@app.get("/api/admin/live_chats")
async def list_live_chats():
    docs = list(live_chat_sessions.find({}, {"_id": 0}))
    # No need to create fallback name - frontend will handle it using student_name
    order = {"queued": 0, "live": 1, "closed": 2}
    docs.sort(key=lambda x: order.get(x.get("status","queued"), 9))
    return docs

# ======================================================================================
#                               PAGES & AUTH
# ======================================================================================
@app.get("/")
def landing(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

# ---- Register (GET + POST) ----
@app.get("/register", response_class=HTMLResponse)
async def get_register(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.post("/register")
async def post_register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    role: str = Form(...)           # keep required (see form below)
):
    if password != confirm_password:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Passwords do not match!"}
        )

    if users_collection.find_one({"email": email}):
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Email already registered!"}
        )

    users_collection.insert_one({
        "full_name": full_name,
        "email": email,
        "password": password,       #TODO: hash this
        "role": role
    })
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "message": "Registration successful! Please login."}
    )


""" @app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request}) """

@app.post("/login")
async def post_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    user = users_collection.find_one({"email": email})
    if user and user["password"] == password and user["role"] == role:
        request.session["user"] = {
            "full_name": user["full_name"],
            "email": user["email"],
            "role": user["role"]
        }
        print("[DEBUG] Session set for user:", request.session["user"])  # Debug log to confirm session set
        if role == "student":
            return RedirectResponse("/student_home", status_code=302)
        elif role == "staff":
            return RedirectResponse("/staff_home", status_code=302)
        elif role == "admin":
            return RedirectResponse("/admin_home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials or role!"})

# Add session validation and role-based access control to protect dashboard routes


# Middleware to validate session and restrict access based on roles
def get_current_user(request: Request):
    user = request.session.get("user")
    print("[DEBUG] Current session user:", user)  # Debug log
    if not user:
        request.session.clear()  # Ensure session is cleared if no user
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not user.get("role") or not user.get("email"):
        request.session.clear()  # Clear stale session
        print("[DEBUG] Stale session cleared")  # Debug log
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

def role_required(required_role: str):
    def role_dependency(user: dict = Depends(get_current_user)):
        if user.get("role") != required_role:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user
    return role_dependency

@app.get("/student_home", response_class=HTMLResponse)
async def student_dashboard(request: Request, user: dict = Depends(role_required("student"))):
    return templates.TemplateResponse("student_home.html", {"request": request})

@app.get("/staff_home", response_class=HTMLResponse)
async def staff_dashboard(request: Request, user: dict = Depends(role_required("staff"))):
    return templates.TemplateResponse("staff_home.html", {"request": request, "user": user})

@app.get("/admin_home", response_class=HTMLResponse)
async def admin_dashboard(request: Request, user: dict = Depends(role_required("admin"))):
    return templates.TemplateResponse("admin_home.html", {"request": request})

@app.get("/knowledge_base", response_class=HTMLResponse)
async def knowledge_base(request: Request, user: dict = Depends(role_required("admin"))):
    return templates.TemplateResponse("knowledge_base.html", {"request": request})

@app.get("/edit_profile", response_class=HTMLResponse)
async def edit_profile(request: Request, user: dict = Depends(role_required("student"))):
    return templates.TemplateResponse("edit_profile.html", {"request": request})

@app.get("/guest_home", response_class=HTMLResponse)
async def guest_dashboard(request: Request, user: dict = Depends(role_required("guest"))):
    return templates.TemplateResponse("guest_home.html", {"request": request})

@app.get("/contact_support", response_class=HTMLResponse)
async def contact_support(request: Request):
    return templates.TemplateResponse("contact_support.html", {"request": request})

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request, user: dict = Depends(get_current_user)):
    if user.get("role") not in ["guest", "student", "admin"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return templates.TemplateResponse("chat.html", {"request": request})


# ------------------ Ticket & Appointment Endpoints ------------------

@app.post("/book_appointment")
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
    attachment: UploadFile | None = File(None)
):
    if not all([department, assigned_staff, subject, date, time_slot, meeting_mode]):
        return JSONResponse({"success": False, "error": "Missing required fields"}, status_code=400)
    
    if not student_email or not student_name:
        return JSONResponse({"success": False, "error": "Student information missing"}, status_code=400)

    # Handle auto-assign to admin for Admin department
    if assigned_staff == "auto-assign-admin":
        admin_user = db.users.find_one({"role": "admin"})
        if admin_user:
            assigned_staff = admin_user.get("email")
            assigned_staff_name = admin_user.get("full_name", admin_user.get("email"))
        else:
            return JSONResponse({"success": False, "error": "Admin user not found"}, status_code=500)
    else:
        # Get staff member's full name for regular assignments
        staff_member = db.users.find_one({"email": assigned_staff})
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
        "confirmation_status": "Awaiting Confirmation"
    }

    try:
        inserted_id = save_appointment(appt, attachment)
        print(f"[DEBUG] /book_appointment: inserted_id={inserted_id} attachment_present={attachment is not None}")
        
        # Notify admins about the new appointment (if scheduled to admin)
        await _notify_admin_appointment_scheduled(appt, str(inserted_id))
        
        # Notify staff about the new appointment (if scheduled to staff)
        await _notify_staff_appointment_scheduled(appt, str(inserted_id))
        
        return {"success": True, "appointment_id": str(inserted_id)}
    except Exception as e:
        print(f"[ERROR] /book_appointment exception: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# Cancel a ticket
@app.post("/api/tickets/cancel/{ticket_id}")
async def cancel_ticket(ticket_id: str):
    try:
        result = db.tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": {"status": "Cancelled", "last_updated": datetime.now().isoformat()}}
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Ticket cancelled successfully."}
        return {"success": False, "message": "Ticket not found or already cancelled."}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# Cancel an appointment
@app.post("/api/appointments/cancel/{appointment_id}")
async def cancel_appointment(appointment_id: str):
    try:
        result = db.appointments.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": {"status": "Cancelled"}}
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Appointment cancelled successfully."}
        return {"success": False, "message": "Appointment not found or already cancelled."}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# Reschedule an appointment
@app.post("/api/appointments/reschedule/{appointment_id}")
async def reschedule_appointment(appointment_id: str, new_date: str, new_time: str):
    try:
        result = db.appointments.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": {"date": new_date, "time": new_time, "status": "Pending Confirmation"}}
        )
        if result.modified_count == 1:
            return {"success": True, "message": "Appointment rescheduled successfully."}
        return {"success": False, "message": "Appointment not found or could not be rescheduled."}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# Debug endpoint to inspect database state from the app's perspective
@app.get("/api/debug")
async def api_debug():
    try:
        cols = db.list_collection_names()
        tickets_count = db.tickets.count_documents({}) if "tickets" in cols else 0
        appts_count = db.appointments.count_documents({}) if "appointments" in cols else 0

        latest_ticket = None
        if tickets_count > 0:
            doc = db.tickets.find_one({}, sort=[("_id", -1)])
            if doc:
                # convert ObjectId fields to strings for JSON
                doc["_id"] = str(doc["_id"])
                if "attachment_id" in doc:
                    doc["attachment_id"] = str(doc["attachment_id"])
                latest_ticket = doc

        return {
            "db": db.name,
            "collections": cols,
            "tickets_count": tickets_count,
            "appointments_count": appts_count,
            "latest_ticket": latest_ticket,
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Return list of tickets (optionally filter by status)
@app.get("/api/tickets")
async def api_tickets(status: str | None = None, student_email: str | None = None):
    try:
        # Build query to exclude cancelled and closed tickets from dashboard
        query = {}
        
        # Filter by student email if provided
        if student_email:
            query["student_email"] = student_email
        
        if status:
            # If status is provided, filter for that specific status
            # but still exclude cancelled tickets
            if status.lower() == "open":
                query["status"] = {"$in": ["Open", "open", "In Progress", "in progress"]}
            else:
                query["status"] = status
        else:
            # If no status provided, exclude cancelled and closed tickets
            query["status"] = {"$nin": ["Cancelled", "cancelled", "Closed", "closed"]}
        
        docs = list(db.tickets.find(query).sort([("_id", -1)]))
        out = []
        for d in docs:
            d["_id"] = str(d["_id"])
            d["date_created"] = d.get("date_created", d.get("created_at", "Unknown"))
            d["last_updated"] = d.get("last_updated", "Unknown")
            d["assigned_staff"] = d.get("assigned_staff") or d.get("assigned_to_name")
            if not d["assigned_staff"]:
                d["assigned_staff"] = "Not Assigned Yet"
            if "attachment_id" in d:
                d["attachment_id"] = str(d["attachment_id"])
            out.append(d)
        return {"count": len(out), "tickets": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# Return list of appointments; if upcoming=true, only return date >= today
@app.get("/api/appointments")
async def api_appointments(upcoming: bool = False, student_email: str | None = None, admin: bool = False):
    try:
        query = {"status": {"$ne": "Cancelled"}}  # Exclude cancelled appointments
        
        # Filter by student email if provided (unless admin is requesting all)
        if student_email and not admin:
            query["student_email"] = student_email
        
        if upcoming:
            today = date.today().isoformat()
            query["date"] = {"$gte": today}
        docs = list(db.appointments.find(query).sort([("date", 1), ("time", 1)]))
        out = []
        for d in docs:
            d["_id"] = str(d["_id"])
            d["status"] = d.get("status", "Pending")
            if d["status"] == "Confirmed":
                appointment_date = datetime.strptime(d["date"], "%Y-%m-%d").date()
                days_left = (appointment_date - date.today()).days
                d["countdown"] = f"In {days_left} days" if days_left > 0 else "Today"
            d["location_mode"] = d.get("location_mode", "Unknown")
            if "attachment_id" in d:
                d["attachment_id"] = str(d["attachment_id"])
            out.append(d)
        return {"count": len(out), "appointments": out}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# Download GridFS attachment by id
@app.get("/api/attachment/{file_id}")
async def api_attachment(file_id: str):
    try:
        grid_out = fs.get(ObjectId(file_id))
        data = grid_out.read()
        return StreamingResponse(io.BytesIO(data), media_type=(grid_out.content_type or "application/octet-stream"), headers={"Content-Disposition": f"attachment; filename=\"{grid_out.filename or file_id}\""})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)


# Adding an endpoint to fetch user details

@app.get("/api/user")
async def get_user_details(request: Request):
    try:
        user = request.session.get("user")
        print("[DEBUG] User details from session:", user)  # Debug log
        if user:
            return {
                "full_name": user.get("full_name"),
                "email": user.get("email"),
                "role": user.get("role")
            }
        return JSONResponse({"error": "User not logged in"}, status_code=401)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# Adding an API endpoint to fetch stats & knowledge base

@app.get("/api/stats")
async def get_stats():
    try:
        knowledge_articles_count = db.knowledge_base.count_documents({})
        departments_count = db.departments.count_documents({"status": "active"})
        total_users_count = db.users.count_documents({})
        upcoming_appointments_count = db.appointments.count_documents({
            "status": {"$ne": "Cancelled"},
            "date": {"$gte": date.today().isoformat()}
        })
        
        return {
            "knowledge_articles": knowledge_articles_count,
            "departments": departments_count,
            "total_users": total_users_count,
            "upcoming_appointments": upcoming_appointments_count
        }
    except Exception as e:
        print(f"Error fetching stats: {e}")
        return {
            "knowledge_articles": 0,
            "departments": 0,
            "total_users": 0,
            "upcoming_appointments": 0
        }

@app.get("/api/knowledge_base")
async def get_knowledge_base():
    try:
        articles = list(db.knowledge_base.find({}, {"_id": 0}))
        return {"articles": articles}
    except Exception as e:
        print(f"Error fetching knowledge base articles: {e}")
        return {"articles": []}


@app.post("/api/knowledge_base")
async def add_knowledge_article(request: Request):
    data = await request.json()
    category = data.get("category")
    title = data.get("title")
    url = data.get("url")

    if not category or not title or not url:
        return JSONResponse({"error": "All fields are required."}, status_code=400)

    try:
        # Extract content from the URL automatically
        from extract_web_content_to_mongo import extract_page, save_to_db
        article = extract_page(url, category, title)
        if not article:
            return JSONResponse({"error": "Failed to fetch content from URL."}, status_code=400)

        # Save to MongoDB
        save_to_db(article)
        return JSONResponse({"message": "Article added successfully."}, status_code=201)
    except Exception as e:
        print(f"Error adding article: {e}")
        return JSONResponse({"error": "Internal server error."}, status_code=500)



# ======================================================================================
#                                  BOT ENDPOINT
# ======================================================================================
@app.post("/chat_question")
async def chat_question(question: str = Form(...)):
    from rag_pipeline import get_answer
    answer, _ = get_answer(question)

    chips, suggest_live_chat, fu_source = build_llm_style_followups(
        user_question=question,
        answer_text=answer or "",
        k=4
    )

    # If bot is unsure, only show "Talk to an admin" button
    if suggest_live_chat:
        chips = [{"label": "Talk to an admin", "payload": {"type": "action", "action": "escalate"}}]

    resp = {
        "answer": answer,
        "suggest_live_chat": suggest_live_chat,
        "suggested_followups": chips
    }
    if DEBUG_FOLLOWUPS:
        resp["followup_generator"] = fu_source  # "openai" | "fallback" | "fallback_error"
    return resp


# Streaming version of chat_question
@app.post("/chat_question_stream")
async def chat_question_stream(question: str = Form(...)):
    from rag_pipeline import get_answer_stream
    
    async def event_generator():
        # Collect full answer for followup generation
        full_answer = ""
        
        # Stream the answer chunks
        for chunk in get_answer_stream(question):
            full_answer += chunk
            # Send each chunk as SSE (Server-Sent Events)
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        
        # Generate followups after answer is complete
        chips, suggest_live_chat, fu_source = build_llm_style_followups(
            user_question=question,
            answer_text=full_answer or "",
            k=4
        )
        
        # If bot is unsure, only show "Talk to an admin" button
        if suggest_live_chat:
            chips = [{"label": "Talk to an admin", "payload": {"type": "action", "action": "escalate"}}]
        
        # Send followups
        followup_data = {
            "type": "followups",
            "suggest_live_chat": suggest_live_chat,
            "suggested_followups": chips
        }
        
        if DEBUG_FOLLOWUPS:
            followup_data["followup_generator"] = fu_source
        
        yield f"data: {json.dumps(followup_data)}\n\n"
        
        # Send done signal
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


# ======================================================================================
#                           TICKET CREATION FROM CHATBOT
# ======================================================================================

class TicketAnalysisRequest(BaseModel):
    message: str

class TicketCreateRequest(BaseModel):
    subject: str
    category: str
    priority: str
    description: str
    student_name: str = ""
    student_email: str = ""

@app.post("/api/analyze_ticket")
async def analyze_ticket_request(request: TicketAnalysisRequest, user: dict = Depends(get_current_user)):
    """
    Analyzes user's message using LLM to extract ticket information
    """
    try:
        from rag_pipeline import get_answer
        import re
        
        # Use LLM to analyze the message
        analysis_prompt = f"""
        Analyze the following user message and extract ticket information.
        
        User message: "{request.message}"
        
        Extract the following:
        1. Subject: A brief subject line (max 100 chars)
        2. Category: One of (Technical Support, Academic, Financial, Housing, Registration, Other)
        3. Priority: One of (Low, Medium, High) - based on urgency in the message
        4. A clear description of the issue
        
        Respond in this exact format:
        SUBJECT: [subject]
        CATEGORY: [category]
        PRIORITY: [priority]
        DESCRIPTION: [description]
        """
        
        # Get LLM analysis
        answer, _ = get_answer(analysis_prompt)
        
        # Parse the response
        subject_match = re.search(r'SUBJECT:\s*(.+)', answer)
        category_match = re.search(r'CATEGORY:\s*(.+)', answer)
        priority_match = re.search(r'PRIORITY:\s*(.+)', answer)
        description_match = re.search(r'DESCRIPTION:\s*(.+)', answer, re.DOTALL)
        
        # Extract values or use defaults
        subject = subject_match.group(1).strip() if subject_match else "Support Request"
        category = category_match.group(1).strip() if category_match else "Other"
        priority = priority_match.group(1).strip() if priority_match else "Medium"
        description = description_match.group(1).strip() if description_match else request.message
        
        # Validate category
        valid_categories = ["Technical Support", "Academic", "Financial", "Housing", "Registration", "Other"]
        if category not in valid_categories:
            category = "Other"
        
        # Validate priority
        valid_priorities = ["Low", "Medium", "High"]
        if priority not in valid_priorities:
            priority = "Medium"
        
        return {
            "subject": subject[:100],  # Limit to 100 chars
            "category": category,
            "priority": priority,
            "description": description
        }
    except Exception as e:
        print(f"Error analyzing ticket: {e}")
        # Fallback to basic extraction
        return {
            "subject": "Support Request",
            "category": "Other",
            "priority": "Medium",
            "description": request.message
        }


@app.post("/api/tickets")
async def create_ticket(ticket: TicketCreateRequest, user: dict = Depends(get_current_user)):
    """
    Creates a new ticket from chatbot
    """
    try:
        # Get student information from session
        student_email = user.get("email", "")
        student_name = user.get("full_name", "")
        
        # Create ticket document
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
            "assigned_to_name": None
        }
        
        # Insert into database
        result = db.tickets.insert_one(ticket_doc)
        
        return {
            "success": True,
            "ticket_id": str(result.inserted_id),
            "message": "Ticket created successfully"
        }
    except Exception as e:
        print(f"Error creating ticket: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================================================================================
#                           MAP INTEGRATION FOR CHATBOT
# ======================================================================================

class MapAnalysisRequest(BaseModel):
    message: str

@app.post("/api/analyze_map_request")
async def analyze_map_request(request: MapAnalysisRequest):
    """
    Analyze user's message to extract location/building information
    """
    try:
        message = request.message.lower()
        
        # Campus buildings database with accurate TAMUCC coordinates from Google Maps
        buildings = {
            "library": {
                "name": "Mary and Jeff Bell Library",
                "lat": 27.713788736691168,
                "lng": -97.32474868648656,
                "address": "6300 Ocean Dr, Corpus Christi, TX 78412",
                "description": "Main campus library with study spaces, computer labs, and research resources",
                "hours": "Mon-Thu 7:30am-midnight, Fri 7:30am-6pm, Sat 10am-6pm, Sun 1pm-midnight",
            },
            "university center": {
                "name": "University Center (UC)",
                "lat": 27.712071037382053,
                "lng": -97.3257065414334,
                "address": "6300 Ocean Dr, Corpus Christi, TX 78412",
                "description": "Student life hub with dining options, Island Grille, and bookstore",
                "hours": "Mon-Fri 7am-10pm, Sat-Sun 10am-8pm",
            },
            "uc": {
                "name": "University Center (UC)",
                "lat": 27.712071037382053,
                "lng": -97.3257065414334,
                "address": "6300 Ocean Dr, Corpus Christi, TX 78412",
                "description": "Student life hub with dining options, Island Grille, and bookstore",
                "hours": "Mon-Fri 7am-10pm, Sat-Sun 10am-8pm",
            },
            "dining": {
                "name": "Islander Dining",
                "lat": 27.711621676963894,
                "lng": -97.32258737277509,
                "address": "Islander Dining, TAMUCC",
                "description": "Campus dining hall with various meal options",
                "hours": "Mon-Fri 7am-8pm, Sat-Sun 10am-7pm",
            },
            "islander dining": {
                "name": "Islander Dining",
                "lat": 27.711621676963894,
                "lng": -97.32258737277509,
                "address": "Islander Dining, TAMUCC",
                "description": "Campus dining hall with various meal options",
                "hours": "Mon-Fri 7am-8pm, Sat-Sun 10am-7pm",
            },
            "natural resources": {
                "name": "Natural Resources Center (NRC)",
                "lat": 27.715332468715157,
                "lng": -97.32880933649331,
                "address": "Natural Resources Center, TAMUCC",
                "description": "College of Science and Engineering, Computer Science departments",
                "hours": "Mon-Fri 8am-5pm",
            },
            "nrc": {
                "name": "Natural Resources Center (NRC)",
                "lat": 27.715332468715157,
                "lng": -97.32880933649331,
                "address": "Natural Resources Center, TAMUCC",
                "description": "College of Science and Engineering, Computer Science departments",
                "hours": "Mon-Fri 8am-5pm",
            },
            "engineering": {
                "name": "Engineering Building",
                "lat": 27.712772225261283,
                "lng": -97.32565431063824,
                "address": "Engineering Building, TAMUCC",
                "description": "College of Science and Engineering, Computer Science departments",
                "hours": "Mon-Fri 8am-5pm",
            },
            "corpus christi hall": {
                "name": "Corpus Christi Hall (CCH)",
                "lat": 27.71516058584113,
                "lng": -97.32370567166191,
                "address": "Corpus Christi Hall, TAMUCC",
                "description": "Academic building with classrooms and faculty offices",
                "hours": "Mon-Fri 7am-10pm",
            },
            "cch": {
                "name": "Corpus Christi Hall (CCH)",
                "lat": 27.71513,
                "lng": -97.32370,
                "address": "Corpus Christi Hall, TAMUCC",
                "description": "Academic building with classrooms and faculty offices",
                "hours": "Mon-Fri 7am-10pm",
            },
            "student services": {
                "name": "Student Services Center",
                "lat": 27.71374042156452,
                "lng": -97.32390201020142,
                "address": "Student Services Center, TAMUCC",
                "description": "Admissions, registrar, financial aid, and student services",
                "hours": "Mon-Fri 8am-5pm",
            },
            "bay hall": {
                "name": "Bay Hall",
                "lat": 27.713613491472024,
                "lng": -97.32348514338884,
                "address": "Bay Hall, TAMUCC",
                "description": "Academic and administrative building",
                "hours": "Mon-Fri 8am-5pm",
            },
            "sciences": {
                "name": "Center for the Sciences",
                "lat": 27.712809298665885,
                "lng": -97.32486990268086,
                "address": "Center for the Sciences, TAMUCC",
                "description": "Science labs and research facilities",
                "hours": "Mon-Fri 8am-5pm",
            },
            "center for sciences": {
                "name": "Center for the Sciences",
                "lat": 27.712809298665885,
                "lng": -97.32486990268086,
                "address": "Center for the Sciences, TAMUCC",
                "description": "Science labs and research facilities",
                "hours": "Mon-Fri 8am-5pm",
            },
            "education": {
                "name": "College of Education and Human Development",
                "lat": 27.713186318706956,
                "lng": -97.32428916719182,
                "address": "College of Education, TAMUCC",
                "description": "Education programs and teacher preparation",
                "hours": "Mon-Fri 8am-5pm",
            },
            "faculty center": {
                "name": "Faculty Center",
                "lat": 27.712820723536026,
                "lng": -97.32358260567656,
                "address": "Faculty Center, TAMUCC",
                "description": "Faculty offices and meeting spaces",
                "hours": "Mon-Fri 8am-5pm",
            },
            "wellness": {
                "name": "Dugan Wellness Center",
                "lat": 27.711601112024837,
                "lng": -97.32413753070178,
                "address": "Dugan Wellness Center, TAMUCC",
                "description": "Student health services and counseling",
                "hours": "Mon-Fri 8am-5pm",

            },
            "dugan": {
                "name": "Dugan Wellness Center",
                "lat": 27.711601112024837,
                "lng": -97.32413753070178,
                "address": "Dugan Wellness Center, TAMUCC",
                "description": "Student health services and counseling",
                "hours": "Mon-Fri 8am-5pm",

            },
            "health": {
                "name": "Dugan Wellness Center",
                "lat": 27.711601112024837,
                "lng": -97.32413753070178,
                "address": "Dugan Wellness Center, TAMUCC",
                "description": "Student health services and counseling",
                "hours": "Mon-Fri 8am-5pm",

            },
            "business": {
                "name": "College of Business",
                "lat": 27.714591440638948,
                "lng": -97.32466461335527,
                "address": "College of Business, TAMUCC",
                "description": "College of Business and entrepreneurship programs",
                "hours": "Mon-Fri 8am-5pm",
            },
            "tidal hall": {
                "name": "Tidal Hall",
                "lat": 27.715529412703646,
                "lng": -97.32710819211944,
                "address": "Tidal Hall, TAMUCC",
                "description": "Student housing residence hall",
                "hours": "24/7 for residents",
            },
            "harte": {
                "name": "Harte Research Institute",
                "lat": 27.713459500631362,
                "lng": -97.32815759566772,
                "address": "Harte Research Institute, TAMUCC",
                "description": "Gulf of Mexico research and marine science",
                "hours": "Mon-Fri 8am-5pm",
            },
            "counseling": {
                "name": "University Counseling Center",
                "lat": 27.712490577148014,
                "lng": -97.32168122550681,
                "address": "University Counseling Center, TAMUCC",
                "description": "Mental health and counseling services for students",
                "hours": "Mon-Fri 8am-5pm",

            },
            "counseling center": {
                "name": "University Counseling Center",
                "lat": 27.712490577148014,
                "lng": -97.32168122550681,
                "address": "University Counseling Center, TAMUCC",
                "description": "Mental health and counseling services for students",
                "hours": "Mon-Fri 8am-5pm",
            }
        }
        
        # Find matching building
        for key, building in buildings.items():
            if key in message or building["name"].lower() in message:
                return {
                    "location": building,
                    "description": f"📍 Here's the location of the **{building['name']}**. {building['description']}."
                }
        
        # No specific building found
        return {
            "location": None,
            "description": "Here's the TAMUCC campus map showing all major buildings."
        }
        
    except Exception as e:
        logging.error(f"Error analyzing map request: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze map request")


class RoutingRequest(BaseModel):
    message: str

@app.post("/api/analyze_routing_request")
async def analyze_routing_request(request: RoutingRequest):
    """
    Analyze user's message to extract origin and destination buildings for routing
    """
    try:
        message = request.message.lower()
        
        # Same buildings database
        buildings = {
            "library": {"name": "Mary and Jeff Bell Library", "lat": 27.713788736691168, "lng": -97.32474868648656},
            "university center": {"name": "University Center (UC)", "lat": 27.712071037382053, "lng": -97.3257065414334},
            "uc": {"name": "University Center (UC)", "lat": 27.712071037382053, "lng": -97.3257065414334},
            "dining": {"name": "Islander Dining", "lat": 27.711621676963894, "lng": -97.32258737277509},
            "islander dining": {"name": "Islander Dining", "lat": 27.711621676963894, "lng": -97.32258737277509},
            "natural resources": {"name": "Natural Resources Center (NRC)", "lat": 27.715332468715157, "lng": -97.32880933649331},
            "nrc": {"name": "Natural Resources Center (NRC)", "lat": 27.715332468715157, "lng": -97.32880933649331},
            "engineering": {"name": "Engineering Building", "lat": 27.712772225261283, "lng": -97.32565431063824},
            "corpus christi hall": {"name": "Corpus Christi Hall (CCH)", "lat": 27.71516058584113, "lng": -97.32370567166191},
            "cch": {"name": "Corpus Christi Hall (CCH)", "lat": 27.71516058584113, "lng": -97.32370567166191},
            "student services": {"name": "Student Services Center", "lat": 27.71374042156452, "lng": -97.32390201020142},
            "bay hall": {"name": "Bay Hall", "lat": 27.713613491472024, "lng": -97.32348514338884},
            "sciences": {"name": "Center for the Sciences", "lat": 27.712809298665885, "lng": -97.32486990268086},
            "center for sciences": {"name": "Center for the Sciences", "lat": 27.712809298665885, "lng": -97.32486990268086},
            "education": {"name": "College of Education and Human Development", "lat": 27.713186318706956, "lng": -97.32428916719182},
            "faculty center": {"name": "Faculty Center", "lat": 27.712820723536026, "lng": -97.32358260567656},
            "wellness": {"name": "Dugan Wellness Center", "lat": 27.711601112024837, "lng": -97.32413753070178},
            "dugan": {"name": "Dugan Wellness Center", "lat": 27.711601112024837, "lng": -97.32413753070178},
            "health": {"name": "Dugan Wellness Center", "lat": 27.711601112024837, "lng": -97.32413753070178},
            "business": {"name": "College of Business", "lat": 27.714591440638948, "lng": -97.32466461335527},
            "tidal hall": {"name": "Tidal Hall", "lat": 27.715529412703646, "lng": -97.32710819211944},
            "harte": {"name": "Harte Research Institute", "lat": 27.713459500631362, "lng": -97.32815759566772},
            "counseling": {"name": "University Counseling Center", "lat": 27.712490577148014, "lng": -97.32168122550681},
            "counseling center": {"name": "University Counseling Center", "lat": 27.712490577148014, "lng": -97.32168122550681},
        }
        
        # Detect routing keywords and extract buildings
        routing_patterns = [
            ("from", "to"),
            ("between", "and"),
            ("get to", "from"),
        ]
        
        origin = None
        destination = None
        
        # Try to find origin and destination
        for pattern in routing_patterns:
            if pattern[0] in message and pattern[1] in message:
                parts = message.split(pattern[0])
                if len(parts) > 1:
                    second_part = parts[1].split(pattern[1])
                    if len(second_part) > 1:
                        # Extract building names
                        origin_text = second_part[0].strip()
                        dest_text = second_part[1].strip()
                        
                        # Find matching buildings
                        for key, building in buildings.items():
                            if key in origin_text or key == origin_text:
                                origin = building
                            if key in dest_text or key == dest_text:
                                destination = building
        
        if origin and destination:
            return {
                "origin": origin,
                "destination": destination,
                "found": True
            }
        else:
            return {
                "origin": None,
                "destination": None,
                "found": False,
                "message": "I couldn't identify both the origin and destination buildings. Please specify like 'directions from Library to UC' or 'how to get from NRC to Wellness Center'."
            }
        
    except Exception as e:
        logging.error(f"Error analyzing routing request: {e}")
        raise HTTPException(status_code=500, detail="Failed to analyze routing request")


# ---------- Google OAuth2 Routes ----------

@app.get("/login/google")
async def login_with_google(request: Request):
    # Use environment variable for redirect URI or default to localhost
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth/google/callback")
async def auth_google_callback(request: Request):
    try:
        # Try to authorize with token
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if user_info:
            # Check if user exists in the database
            user = users_collection.find_one({"email": user_info["email"]})

            if not user:
                # Register the user if they don't exist
                users_collection.insert_one({
                    "full_name": user_info.get("name"),
                    "email": user_info.get("email"),
                    "role": "guest",  # Default role for Google SSO users
                    "created_at": datetime.utcnow()
                })

            # Store user info in the session
            request.session["user"] = {
                "full_name": user_info.get("name"),
                "email": user_info.get("email"),
                "role": user.get("role", "guest") if user else "guest"
            }

            # Redirect to the appropriate dashboard
            return RedirectResponse(url="/guest_home")

        return RedirectResponse(url="/login")
    
    except Exception as e:
        # Handle state mismatch or other OAuth errors
        print(f"[ERROR] OAuth callback failed: {e}")
        
        # For development: try to get user info directly from the code
        # This is a fallback for state mismatch issues
        try:
            # Get code from query params
            code = request.query_params.get("code")
            if code:
                # Exchange code for token manually
                import httpx
                token_url = "https://oauth2.googleapis.com/token"
                redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
                
                async with httpx.AsyncClient() as client:
                    response = await client.post(token_url, data={
                        "code": code,
                        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code"
                    })
                    
                    if response.status_code == 200:
                        token_data = response.json()
                        access_token = token_data.get("access_token")
                        
                        # Get user info
                        userinfo_response = await client.get(
                            "https://www.googleapis.com/oauth2/v2/userinfo",
                            headers={"Authorization": f"Bearer {access_token}"}
                        )
                        
                        if userinfo_response.status_code == 200:
                            user_info = userinfo_response.json()
                            
                            # Check if user exists in the database
                            user = users_collection.find_one({"email": user_info["email"]})

                            if not user:
                                # Register the user if they don't exist
                                users_collection.insert_one({
                                    "full_name": user_info.get("name"),
                                    "email": user_info.get("email"),
                                    "role": "guest",
                                    "created_at": datetime.utcnow()
                                })

                            # Store user info in the session
                            request.session["user"] = {
                                "full_name": user_info.get("name"),
                                "email": user_info.get("email"),
                                "role": user.get("role", "guest") if user else "guest"
                            }
                            
                            print(f"[SUCCESS] Manual OAuth flow succeeded for {user_info.get('email')}")
                            return RedirectResponse(url="/guest_home")
        except Exception as fallback_error:
            print(f"[ERROR] Fallback OAuth also failed: {fallback_error}")
        
        # If everything fails, redirect back to login
        return RedirectResponse(url="/login?error=oauth_failed")

# Ensure session is completely cleared on logout
@app.get("/logout")
async def logout(request: Request):
    request.session.clear()  # Clear the session completely
    print("[DEBUG] Session after clearing:", request.session)  # Debug log to confirm session is empty
    return RedirectResponse(url="/login")

# API endpoint to fetch courses by term
@app.get("/api/courses/{term}")
def get_courses(term: str):
    courses = list(db.courses.find({"term": term}))
    return convert_objectid_to_str(courses)

# Helper function to convert ObjectId to string
def convert_objectid_to_str(doc):
    if isinstance(doc, list):
        return [convert_objectid_to_str(d) for d in doc]
    elif isinstance(doc, dict):
        return {k: convert_objectid_to_str(v) for k, v in doc.items()}
    elif isinstance(doc, ObjectId):
        return str(doc)
    return doc

# Define a Pydantic model for course registration
class CourseRegistration(BaseModel):
    student_email: str
    course_id: str
    term: str

# Updated API endpoint to register a student for a course
@app.post("/api/register_course")
def register_course(registration: CourseRegistration):
    try:
        # Validate the registration data
        registration_data = registration.dict()
        db.registrations.insert_one(registration_data)
        return {"message": "Registration successful"}
    except ValidationError as e:
        return JSONResponse({"error": "Invalid registration data", "details": e.errors()}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# API endpoint to fetch registered courses for a student
@app.get("/api/registered_courses/{student_email}")
def get_registered_courses(student_email: str):
    registrations = list(db.registrations.find({"student_email": student_email}))
    registered_courses = []

    for registration in registrations:
        course = db.courses.find_one({"_id": ObjectId(registration["course_id"])});
        if course:
            course["_id"] = str(course["_id"])  # Convert ObjectId to string
            registration["course_details"] = {
                "title": course.get("title", "N/A"),
                "details": course.get("details", "N/A"),
                "hours": course.get("hours", "N/A"),
                "crn": course.get("crn", "N/A"),
                "schedule_type": course.get("schedule_type", "N/A"),
                "grade_mode": course.get("grade_mode", "N/A"),
                "level": course.get("level", "N/A"),
                "part_of_term": course.get("part_of_term", "N/A"),
            }
        registration["_id"] = str(registration["_id"])  # Convert ObjectId to string
        registered_courses.append(registration)

    return registered_courses

# API endpoint to fetch student data
@app.get("/api/student/{email}")
def get_student(email: str):
    student = db.users.find_one({"email": email})
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    student["_id"] = str(student["_id"])  # Convert ObjectId to string
    return student

# Define a Pydantic model for student data
class StudentUpdate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: str
    marital_status: str
    legal_sex: str
    email: str
    phone_number: str
    address: str

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("update_student")

# Add a test log at the start of the file to verify logging
logger.info("Test log: Logging is working.")

# Updated API endpoint to update student data with full_name auto-generation
@app.put("/api/student/{email}")
def update_student(email: str, student_data: StudentUpdate):
    try:
        logger.info(f"Received update request for email: {email}")
        logger.info(f"Request payload: {student_data.dict()}")

        # Generate full_name separately
        full_name = f"{student_data.first_name} {student_data.last_name}".strip()

        # Update the database with the student data and full_name
        update_data = student_data.dict()
        update_data["full_name"] = full_name

        result = db.users.update_one({"email": email}, {"$set": update_data}, upsert=True)
        if result.modified_count == 0 and not result.upserted_id:
            logger.error("Failed to update student data in the database.")
            raise HTTPException(status_code=400, detail="Failed to update student data")

        logger.info("Student data updated successfully.")
        return {"message": "Student data updated successfully"}
    except Exception as e:
        logger.exception("An error occurred while updating student data.")
        raise HTTPException(status_code=500, detail="Internal server error")

# API endpoint to fetch registered classes for a student
@app.get("/api/student/{email}/registered_classes")
def get_registered_classes(email: str):
    registrations = list(db.registrations.find({"student_email": email}))
    registered_classes = []

    for registration in registrations:
        course = db.courses.find_one({"_id": ObjectId(registration["course_id"])})
        if course:
            course["_id"] = str(course["_id"])  # Convert ObjectId to string
            registration["course_details"] = course
        registration["_id"] = str(registration["_id"])  # Convert ObjectId to string
        registered_classes.append(registration)

    return registered_classes

# API endpoint to get all staff members (for admin to assign tickets)
@app.get("/api/staff")
def get_all_staff():
    try:
        staff_members = list(db.users.find(
            {"role": "staff", "status": "active"},
            {"password": 0}  # Exclude password from response
        ))
        return convert_objectid_to_str(staff_members)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API endpoint to get staff by department
@app.get("/api/staff/department/{department}")
def get_staff_by_department(department: str):
    try:
        staff_members = list(db.users.find(
            {"role": "staff", "department": department, "status": "active"},
            {"password": 0}
        ))
        return convert_objectid_to_str(staff_members)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API endpoint to get individual ticket details
@app.get("/api/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    try:
        from bson import ObjectId
        
        ticket = db.tickets.find_one({"_id": ObjectId(ticket_id)})
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        ticket["_id"] = str(ticket["_id"])
        ticket["date_created"] = ticket.get("date_created", ticket.get("created_at", "Unknown"))
        ticket["last_updated"] = ticket.get("last_updated", "Unknown")
        
        if "attachment_id" in ticket:
            ticket["attachment_id"] = str(ticket["attachment_id"])
        
        return ticket
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API endpoint to get individual appointment details
@app.get("/api/appointments/{appointment_id}")
async def get_appointment(appointment_id: str):
    try:
        from bson import ObjectId
        
        appointment = db.appointments.find_one({"_id": ObjectId(appointment_id)})
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        appointment["_id"] = str(appointment["_id"])
        
        return appointment
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API endpoint to assign ticket to staff
@app.put("/api/tickets/{ticket_id}/assign")
def assign_ticket(ticket_id: str, staff_email: str):
    try:
        from bson import ObjectId
        
        # Verify staff exists
        staff = db.users.find_one({"email": staff_email, "role": "staff"})
        if not staff:
            raise HTTPException(status_code=404, detail="Staff member not found")
        
        # Update ticket
        result = db.tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {
                "$set": {
                    "assigned_to": staff_email,
                    "assigned_to_name": staff.get("full_name"),
                    "status": "assigned",
                    "assigned_at": datetime.now().isoformat()
                }
            }
        )
        
        if result.modified_count == 0:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        return {"message": "Ticket assigned successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# API endpoint to update ticket (status and/or assigned staff)
@app.put("/api/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        # Get ticket details first
        ticket = db.tickets.find_one({"_id": ObjectId(ticket_id)})
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        data = await request.json()
        status = data.get("status")
        assigned_staff = data.get("assigned_staff")
        
        update_fields = {
            "last_updated": datetime.now().isoformat()
        }
        
        notification_action = None
        
        if status:
            update_fields["status"] = status
            if status.lower() == "resolved":
                notification_action = "resolved"
            else:
                notification_action = "updated"
        
        if assigned_staff and assigned_staff != "":
            # Verify staff exists
            staff = db.users.find_one({"email": assigned_staff, "role": "staff"})
            if staff:
                update_fields["assigned_staff"] = assigned_staff
                update_fields["assigned_to_name"] = staff.get("full_name")
                update_fields["assigned_at"] = datetime.now().isoformat()
                if not notification_action:
                    notification_action = "assigned"
        
        # Update ticket
        result = db.tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": update_fields}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Ticket not found")
        
        # Create notification if there's an action
        if notification_action:
            # Merge update_fields with original ticket for notification
            updated_ticket = {**ticket, **update_fields}
            
            # Notify student about ticket changes
            await _create_ticket_notification(updated_ticket, ticket_id, notification_action)
            
            # If ticket is closed/resolved, notify assigned staff AND admins
            if status and status.lower() in ["closed", "resolved"]:
                # Pass current user email to avoid self-notification
                current_user_email = user.get("email")
                await _notify_staff_ticket_closed(updated_ticket, ticket_id, current_user_email)
                await _notify_admin_ticket_resolved(updated_ticket, ticket_id)
        
        return {"message": "Ticket updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== APPOINTMENTS MANAGEMENT ====================

@app.put("/api/appointments/{appointment_id}")
async def update_appointment(appointment_id: str, request: Request):
    try:
        from bson import ObjectId
        
        data = await request.json()
        
        update_fields = {
            "last_updated": datetime.now().isoformat()
        }
        
        # Update modifiable fields
        if data.get("date"):
            update_fields["date"] = data["date"]
        if data.get("time_slot"):
            update_fields["time_slot"] = data["time_slot"]
        if data.get("meeting_mode"):
            update_fields["meeting_mode"] = data["meeting_mode"]
        if data.get("location_mode"):
            update_fields["location_mode"] = data["location_mode"]
        
        # Update appointment
        result = db.appointments.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": update_fields}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        return {"message": "Appointment updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/appointments/{appointment_id}/confirm")
async def confirm_appointment(appointment_id: str):
    try:
        from bson import ObjectId
        
        # Get appointment details first
        appointment = db.appointments.find_one({"_id": ObjectId(appointment_id)})
        if not appointment:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        update_fields = {
            "status": "Confirmed",
            "confirmation_status": "Confirmed",
            "confirmed_at": datetime.now().isoformat(),
            "last_updated": datetime.now().isoformat()
        }
        
        # Update appointment
        result = db.appointments.update_one(
            {"_id": ObjectId(appointment_id)},
            {"$set": update_fields}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Appointment not found")
        
        # Auto-create notification for student
        await _create_appointment_notification(appointment, appointment_id, "confirmed")
        
        return {"message": "Appointment confirmed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== DEPARTMENTS MANAGEMENT ====================

# Get all departments
@app.get("/api/departments")
async def get_departments(status: str | None = None):
    try:
        query = {}
        if status:
            query["status"] = status
        else:
            query["status"] = "active"  # Default to active departments
        
        departments = list(db.departments.find(query).sort("name", 1))
        return convert_objectid_to_str(departments)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get all students for user management
@app.get("/api/students")
async def get_all_students():
    try:
        students = list(db.users.find(
            {"role": "student"},
            {"password": 0}  # Exclude password from response
        ).sort("full_name", 1))
        return convert_objectid_to_str(students)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get single department by ID
@app.get("/api/departments/{department_id}")
async def get_department(department_id: str):
    try:
        department = db.departments.find_one({"department_id": department_id})
        if not department:
            raise HTTPException(status_code=404, detail="Department not found")
        return convert_objectid_to_str(department)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Create new department
@app.post("/api/departments")
async def create_department(request: Request):
    try:
        data = await request.json()
        
        # Check if department_id already exists
        existing = db.departments.find_one({"department_id": data.get("department_id")})
        if existing:
            raise HTTPException(status_code=400, detail="Department ID already exists")
        
        result = db.departments.insert_one(data)
        return {"message": "Department created successfully", "id": str(result.inserted_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Update department
@app.put("/api/departments/{department_id}")
async def update_department(department_id: str, request: Request):
    try:
        data = await request.json()
        
        result = db.departments.update_one(
            {"department_id": department_id},
            {"$set": data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Department not found")
        
        return {"message": "Department updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Delete department (soft delete by setting status to inactive)
@app.delete("/api/departments/{department_id}")
async def delete_department(department_id: str):
    try:
        result = db.departments.update_one(
            {"department_id": department_id},
            {"$set": {"status": "inactive"}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Department not found")
        
        return {"message": "Department deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================================================================================
#                           NOTIFICATION SYSTEM API ENDPOINTS
# ======================================================================================

# ======================================================================================
#                              AUTO-TRIGGER HELPER FUNCTIONS
# ======================================================================================

async def _create_appointment_notification(appointment: dict, appointment_id: str, action: str):
    """Create notification when appointment is confirmed, updated, or cancelled"""
    try:
        student_email = appointment.get("student_email")
        if not student_email:
            return
        
        # Build notification based on action
        if action == "confirmed":
            title = "✅ Appointment Confirmed"
            message = f"Your appointment '{appointment.get('subject')}' on {appointment.get('date')} at {appointment.get('time_slot')} has been confirmed."
            priority = "normal"
        elif action == "updated":
            title = "🔄 Appointment Updated"
            message = f"Your appointment '{appointment.get('subject')}' has been updated. Please check the details."
            priority = "normal"
        elif action == "cancelled":
            title = "❌ Appointment Cancelled"
            message = f"Your appointment '{appointment.get('subject')}' has been cancelled."
            priority = "high"
        else:
            return
        
        notification = {
            "user_email": student_email,
            "type": "appointment",
            "title": title,
            "message": message,
            "priority": priority,
            "related_id": appointment_id,
            "link": "/student_home",
            "status": "unread",
            "created_at": datetime.now().isoformat()
        }
        
        db.notifications.insert_one(notification)
        print(f"[INFO] Created {action} notification for appointment {appointment_id}")
    except Exception as e:
        print(f"[ERROR] Failed to create appointment notification: {e}")

async def _create_ticket_notification(ticket: dict, ticket_id: str, action: str):
    """Create notification when ticket status changes"""
    try:
        student_email = ticket.get("student_email")
        if not student_email:
            return
        
        # Build notification based on action
        if action == "assigned":
            title = "📋 Ticket Assigned"
            message = f"Your ticket '{ticket.get('subject')}' has been assigned to {ticket.get('assigned_to_name', 'a staff member')}."
            priority = "normal"
        elif action == "resolved":
            title = "✅ Ticket Resolved"
            message = f"Your ticket '{ticket.get('subject')}' has been marked as resolved."
            priority = "normal"
        elif action == "updated":
            title = "🔄 Ticket Updated"
            message = f"Your ticket '{ticket.get('subject')}' status has been updated to {ticket.get('status')}."
            priority = "normal"
        else:
            return
        
        notification = {
            "user_email": student_email,
            "type": "ticket",
            "title": title,
            "message": message,
            "priority": priority,
            "related_id": ticket_id,
            "link": "/student_home",
            "status": "unread",
            "created_at": datetime.now().isoformat()
        }
        
        db.notifications.insert_one(notification)
        print(f"[INFO] Created {action} notification for ticket {ticket_id}")
    except Exception as e:
        print(f"[ERROR] Failed to create ticket notification: {e}")

async def _notify_admin_new_ticket(ticket: dict, ticket_id: str):
    """Notify admin when a new ticket is raised"""
    try:
        # Get all admins
        admins = list(db.users.find({"role": "admin"}, {"email": 1}))
        
        for admin in admins:
            notification = {
                "user_email": admin["email"],
                "type": "ticket",
                "title": "🎫 New Ticket Raised",
                "message": f"New {ticket.get('priority', 'normal')} priority ticket from {ticket.get('student_name')}: {ticket.get('subject')}",
                "priority": "normal" if ticket.get('priority', 'normal').lower() != 'urgent' else "high",
                "related_id": ticket_id,
                "link": "/admin_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        # Also notify preferred staff if specified
        if ticket.get("preferred_staff"):
            notification = {
                "user_email": ticket["preferred_staff"],
                "type": "ticket",
                "title": "🎫 New Ticket Assigned to You",
                "message": f"New {ticket.get('priority', 'normal')} priority ticket from {ticket.get('student_name')}: {ticket.get('subject')}",
                "priority": "normal" if ticket.get('priority', 'normal').lower() != 'urgent' else "high",
                "related_id": ticket_id,
                "link": "/staff_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        print(f"[INFO] Notified admins about new ticket {ticket_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify admins about new ticket: {e}")

async def _notify_staff_ticket_closed(ticket: dict, ticket_id: str, closed_by_email: str = None):
    """Notify assigned staff when a ticket is closed (only if not closed by themselves)"""
    try:
        if not ticket.get("assigned_staff"):
            return
        
        # Don't notify staff if they are the ones who closed the ticket
        if closed_by_email and ticket["assigned_staff"] == closed_by_email:
            print(f"[INFO] Staff closed their own ticket, skipping self-notification")
            return
        
        notification = {
            "user_email": ticket["assigned_staff"],
            "type": "ticket",
            "title": "✅ Ticket Closed",
            "message": f"Ticket '{ticket.get('subject')}' from {ticket.get('student_name')} has been closed.",
            "priority": "low",
            "related_id": ticket_id,
            "link": "/staff_home",
            "status": "unread",
            "created_at": datetime.now().isoformat()
        }
        
        db.notifications.insert_one(notification)
        print(f"[INFO] Notified staff about closed ticket {ticket_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify staff about closed ticket: {e}")

async def _notify_admin_ticket_resolved(ticket: dict, ticket_id: str):
    """Notify admins when a ticket is resolved"""
    try:
        # Get all admin users
        admins = list(db.users.find({"role": "admin"}))
        
        for admin in admins:
            notification = {
                "user_email": admin["email"],
                "type": "ticket",
                "title": "✅ Ticket Resolved",
                "message": f"Ticket '{ticket.get('subject')}' from {ticket.get('student_name')} has been resolved by staff.",
                "priority": "low",
                "related_id": ticket_id,
                "link": "/admin_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        print(f"[INFO] Notified admins about resolved ticket {ticket_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify admins about resolved ticket: {e}")

async def _notify_admin_appointment_scheduled(appointment: dict, appointment_id: str):
    """Notify admins when a new appointment is scheduled with Admin department"""
    try:
        # Only notify if appointment is assigned to admin (check department or assigned_staff_name)
        assigned_staff_name = appointment.get('assigned_staff_name', '')
        department = appointment.get('department', '')
        
        # Check if it's scheduled to Admin
        if assigned_staff_name != "Admin" and department.lower() != "admin":
            print(f"[INFO] Appointment not scheduled to Admin, skipping admin notification")
            return
        
        # Get all admin users
        admins = list(db.users.find({"role": "admin"}))
        
        for admin in admins:
            notification = {
                "user_email": admin["email"],
                "type": "appointment",
                "title": "📅 New Appointment Scheduled",
                "message": f"New appointment scheduled by {appointment.get('student_name')} for {appointment.get('subject')} on {appointment.get('date')}.",
                "priority": "normal",
                "related_id": appointment_id,
                "link": "/admin_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        print(f"[INFO] Notified admins about new appointment {appointment_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify admins about new appointment: {e}")

async def _notify_staff_appointment_scheduled(appointment: dict, appointment_id: str):
    """Notify staff when an appointment is scheduled with them"""
    try:
        assigned_staff = appointment.get('assigned_staff')
        
        # Only notify if there's an assigned staff and it's not admin
        if not assigned_staff:
            return
        
        # Check if assigned staff is actually a staff member (not admin)
        staff_user = db.users.find_one({"email": assigned_staff, "role": "staff"})
        if not staff_user:
            print(f"[INFO] Assigned staff is not a staff member, skipping staff notification")
            return
        
        notification = {
            "user_email": assigned_staff,
            "type": "appointment",
            "title": "📅 New Appointment Assigned",
            "message": f"New appointment scheduled by {appointment.get('student_name')} for {appointment.get('subject')} on {appointment.get('date')}.",
            "priority": "normal",
            "related_id": appointment_id,
            "link": "/staff_home",
            "status": "unread",
            "created_at": datetime.now().isoformat()
        }
        
        db.notifications.insert_one(notification)
        print(f"[INFO] Notified staff {assigned_staff} about new appointment {appointment_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify staff about new appointment: {e}")

async def _notify_event_completed(event: dict, event_id: str):
    """Notify target audience when an event is marked as completed"""
    try:
        target_audience = event.get('target_audience', 'all')
        
        # Determine which users to notify based on target audience
        users_to_notify = []
        
        if target_audience == 'all':
            # Notify all users (students, staff, admin)
            users_to_notify = list(db.users.find({}))
        elif target_audience == 'students':
            users_to_notify = list(db.users.find({"role": "student"}))
        elif target_audience == 'staff':
            users_to_notify = list(db.users.find({"role": "staff"}))
        elif target_audience == 'admin':
            users_to_notify = list(db.users.find({"role": "admin"}))
        
        # Create notifications for each user
        for user in users_to_notify:
            notification = {
                "user_email": user["email"],
                "type": "event",
                "title": "✅ Event Completed",
                "message": f"The event '{event.get('title')}' has been marked as completed.",
                "priority": "low",
                "related_id": event_id,
                "link": f"/{user.get('role', 'student')}_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        print(f"[INFO] Notified {len(users_to_notify)} users about completed event {event_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify users about completed event: {e}")


# Pydantic models for notifications
class NotificationCreate(BaseModel):
    user_email: str
    type: str  # "appointment", "ticket", "event", "system"
    title: str
    message: str
    priority: str = "normal"  # "low", "normal", "high", "urgent"
    related_id: str | None = None  # ID of related appointment/ticket/event
    link: str | None = None  # Optional link to related page

# Create a notification
@app.post("/api/notifications/create")
async def create_notification(notification: NotificationCreate, user: dict = Depends(get_current_user)):
    try:
        # Only admin and staff can create notifications manually
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
            "created_by": user.get("email")
        }
        
        result = db.notifications.insert_one(notification_doc)
        
        return {
            "success": True,
            "notification_id": str(result.inserted_id),
            "message": "Notification created successfully"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get user's notifications
@app.get("/api/notifications")
async def get_notifications(
    request: Request,
    status: str | None = None,
    limit: int = 50,
    user: dict = Depends(get_current_user)
):
    try:
        user_email = user.get("email")
        
        # Build query
        query = {"user_email": user_email}
        if status:
            query["status"] = status
        
        # Fetch notifications sorted by creation date (newest first)
        notifications = list(
            db.notifications.find(query)
            .sort("created_at", -1)
            .limit(limit)
        )
        
        return convert_objectid_to_str(notifications)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mark notification as read
@app.put("/api/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        user_email = user.get("email")
        
        # Update only if notification belongs to user
        result = db.notifications.update_one(
            {"_id": ObjectId(notification_id), "user_email": user_email},
            {"$set": {"status": "read", "read_at": datetime.now().isoformat()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        return {"success": True, "message": "Notification marked as read"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mark all notifications as read
@app.put("/api/notifications/mark-all-read")
async def mark_all_notifications_read(user: dict = Depends(get_current_user)):
    try:
        user_email = user.get("email")
        
        # Update all unread notifications for this user
        result = db.notifications.update_many(
            {"user_email": user_email, "status": "unread"},
            {"$set": {"status": "read", "read_at": datetime.now().isoformat()}}
        )
        
        return {
            "success": True,
            "message": f"Marked {result.modified_count} notifications as read",
            "count": result.modified_count
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Delete notification
@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        user_email = user.get("email")
        
        # Delete only if notification belongs to user
        result = db.notifications.delete_one(
            {"_id": ObjectId(notification_id), "user_email": user_email}
        )
        
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Notification not found")
        
        return {"success": True, "message": "Notification deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get unread notification count
@app.get("/api/notifications/unread/count")
async def get_unread_count(user: dict = Depends(get_current_user)):
    try:
        user_email = user.get("email")
        
        count = db.notifications.count_documents({
            "user_email": user_email,
            "status": "unread"
        })
        
        return {"count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================================================================================
#                              EVENT MANAGEMENT API ENDPOINTS
# ======================================================================================

# Pydantic models for events
class EventCreate(BaseModel):
    title: str
    description: str
    event_date: str
    event_time: str | None = None
    priority: str = "normal"  # "low", "normal", "high", "urgent"
    target_audience: str = "all"  # "all", "students", "staff", "specific"
    specific_emails: list[str] | None = None  # For specific users
    category: str = "general"  # "general", "academic", "administrative", "social"

# Create an event
@app.post("/api/events/create")
async def create_event(event: EventCreate, user: dict = Depends(get_current_user)):
    try:
        # Only admin and staff can create events
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Only admin and staff can create events")
        
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
            "created_by_name": user.get("full_name")
        }
        
        result = db.events.insert_one(event_doc)
        event_id = str(result.inserted_id)
        
        # Auto-create notifications for target users
        await _create_event_notifications(event_doc, event_id)
        
        return {
            "success": True,
            "event_id": event_id,
            "message": "Event created and notifications sent"
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR] Failed to create event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create event: {str(e)}")

# Helper function to create event notifications
async def _create_event_notifications(event: dict, event_id: str):
    """Create notifications for event based on target audience"""
    try:
        target_users = []
        
        if event["target_audience"] == "all":
            # Notify all users
            target_users = list(db.users.find({}, {"email": 1}))
        elif event["target_audience"] == "students":
            # Notify all students
            target_users = list(db.users.find({"role": "student"}, {"email": 1}))
        elif event["target_audience"] == "staff":
            # Notify all staff
            target_users = list(db.users.find({"role": "staff"}, {"email": 1}))
        elif event["target_audience"] == "specific" and event.get("specific_emails"):
            # Notify specific users
            target_users = [{"email": email} for email in event["specific_emails"]]
        
        # Create notification for each target user
        notifications = []
        for user in target_users:
            notification = {
                "user_email": user["email"],
                "type": "event",
                "title": f"New Event: {event['title']}",
                "message": event["description"],
                "priority": event["priority"],
                "related_id": event_id,
                "link": f"/events/{event_id}",
                "status": "unread",
                "created_at": datetime.now().isoformat(),
                "event_date": event["event_date"],
                "event_time": event.get("event_time")
            }
            notifications.append(notification)
        
        if notifications:
            db.notifications.insert_many(notifications)
            print(f"[INFO] Created {len(notifications)} event notifications for event {event_id}")
    except Exception as e:
        print(f"[ERROR] Failed to create event notifications: {e}")

# Get all events
@app.get("/api/events")
async def get_events(
    status: str | None = None,
    category: str | None = None,
    user: dict = Depends(get_current_user)
):
    try:
        query = {}
        
        # Filter by status
        if status:
            query["status"] = status
        else:
            query["status"] = {"$ne": "deleted"}
        
        # Filter by category
        if category:
            query["category"] = category
        
        # Fetch events sorted by event date
        events = list(
            db.events.find(query)
            .sort("event_date", -1)
        )
        
        return convert_objectid_to_str(events)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Update an event
@app.put("/api/events/{event_id}")
async def update_event(event_id: str, request: Request, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        # Only admin and staff can update events
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Only admin and staff can update events")
        
        data = await request.json()
        
        # Add update tracking
        data["updated_at"] = datetime.now().isoformat()
        data["updated_by"] = user.get("email")
        
        result = db.events.update_one(
            {"_id": ObjectId(event_id)},
            {"$set": data}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Event not found")
        
        return {"success": True, "message": "Event updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Delete an event (soft delete)
@app.delete("/api/events/{event_id}")
async def delete_event(event_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        # Only admin and staff can delete events
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Only admin and staff can delete events")
        
        result = db.events.update_one(
            {"_id": ObjectId(event_id)},
            {"$set": {
                "status": "deleted",
                "deleted_at": datetime.now().isoformat(),
                "deleted_by": user.get("email")
            }}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Event not found")
        
        return {"success": True, "message": "Event deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mark event as complete
@app.put("/api/events/{event_id}/complete")
async def mark_event_complete(event_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        # Only admin and staff can mark events complete
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Only admin and staff can mark events complete")
        
        # Get event details first
        event = db.events.find_one({"_id": ObjectId(event_id)})
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        
        result = db.events.update_one(
            {"_id": ObjectId(event_id)},
            {"$set": {
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
                "completed_by": user.get("email")
            }}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Event not found")
        
        # Send notifications to target audience
        await _notify_event_completed(event, event_id)
        
        return {"success": True, "message": "Event marked as complete"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==================== SURVEYS / FEEDBACK MANAGEMENT ====================

# Pydantic models for surveys
class SurveyQuestionCreate(BaseModel):
    question_id: str
    question_text: str
    question_type: str  # "rating", "multiple_choice", "text", "yes_no"
    options: list[str] | None = None  # For multiple_choice
    required: bool = True
    order: int

class SurveyCreate(BaseModel):
    title: str
    description: str | None = None
    survey_type: str  # "course_evaluation", "service_feedback", "general", "event", "custom"
    target_audience: str = "all"  # "all", "students", "staff"
    questions: list[SurveyQuestionCreate]
    start_date: str
    end_date: str
    is_anonymous: bool = True

class SurveyAnswerSubmit(BaseModel):
    question_id: str
    answer: str | int

class SurveyResponseSubmit(BaseModel):
    answers: list[SurveyAnswerSubmit]

# Create survey (Admin/Staff only)
@app.post("/api/surveys/create")
async def create_survey(survey: SurveyCreate, user: dict = Depends(get_current_user)):
    try:
        # Only admin and staff can create surveys
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Only admin and staff can create surveys")
        
        survey_doc = {
            "title": survey.title,
            "description": survey.description,
            "survey_type": survey.survey_type,
            "status": "active",  # Can be "draft", "active", "closed"
            "target_audience": survey.target_audience,
            "questions": [q.dict() for q in survey.questions],
            "start_date": survey.start_date,
            "end_date": survey.end_date,
            "is_anonymous": survey.is_anonymous,
            "created_by": user.get("email"),
            "created_by_name": user.get("full_name", user.get("email")),
            "created_at": datetime.now().isoformat(),
            "total_responses": 0
        }
        
        result = db.surveys.insert_one(survey_doc)
        
        # Notify target audience about new survey
        await _notify_survey_available(survey_doc, str(result.inserted_id))
        
        return {"success": True, "message": "Survey created successfully", "survey_id": str(result.inserted_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get all surveys (Admin/Staff)
@app.get("/api/surveys")
async def get_surveys(user: dict = Depends(get_current_user)):
    try:
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        surveys = list(db.surveys.find().sort("created_at", -1))
        return convert_objectid_to_str(surveys)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get available surveys for current user (Students)
@app.get("/api/surveys/available")
async def get_available_surveys(user: dict = Depends(get_current_user)):
    try:
        user_email = user.get("email")
        user_role = user.get("role")
        
        # Get active surveys
        query = {
            "status": "active",
            "end_date": {"$gte": datetime.now().isoformat()}
        }
        
        # Filter by target audience
        if user_role == "student":
            query["$or"] = [
                {"target_audience": "all"},
                {"target_audience": "students"}
            ]
        elif user_role == "staff":
            query["$or"] = [
                {"target_audience": "all"},
                {"target_audience": "staff"}
            ]
        
        surveys = list(db.surveys.find(query).sort("created_at", -1))
        
        # Check which surveys the user has already responded to
        for survey in surveys:
            survey_id = str(survey["_id"])
            response = db.survey_responses.find_one({
                "survey_id": survey_id,
                "respondent_email": user_email
            })
            survey["already_responded"] = response is not None
        
        return convert_objectid_to_str(surveys)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get count of surveys submitted by student
@app.get("/api/surveys/submitted/count")
async def get_submitted_surveys_count(user: dict = Depends(get_current_user)):
    try:
        user_email = user.get("email")
        count = db.survey_responses.count_documents({"respondent_email": user_email})
        return {"count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get single survey details
@app.get("/api/surveys/{survey_id}")
async def get_survey(survey_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        survey = db.surveys.find_one({"_id": ObjectId(survey_id)})
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        
        return convert_objectid_to_str(survey)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Submit survey response
@app.post("/api/surveys/{survey_id}/submit")
async def submit_survey_response(survey_id: str, response: SurveyResponseSubmit, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        user_email = user.get("email")
        
        # Check if survey exists
        survey = db.surveys.find_one({"_id": ObjectId(survey_id)})
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        
        # Check if user already responded
        existing_response = db.survey_responses.find_one({
            "survey_id": survey_id,
            "respondent_email": user_email
        })
        
        if existing_response:
            raise HTTPException(status_code=400, detail="You have already submitted this survey")
        
        # Create response document
        # Note: We ALWAYS save the actual email to track completion and prevent duplicates
        # For anonymous surveys, we just hide the identity when displaying results
        response_doc = {
            "survey_id": survey_id,
            "respondent_email": user_email,  # Always save actual email for tracking
            "respondent_name": user.get("full_name", user.get("email")) if not survey.get("is_anonymous") else "Anonymous",
            "respondent_role": user.get("role"),
            "is_anonymous": survey.get("is_anonymous", False),  # Flag to know if this should be shown anonymously
            "answers": [a.dict() for a in response.answers],
            "submitted_at": datetime.now().isoformat()
        }
        
        db.survey_responses.insert_one(response_doc)
        
        # Update survey response count
        db.surveys.update_one(
            {"_id": ObjectId(survey_id)},
            {"$inc": {"total_responses": 1}}
        )
        
        return {"success": True, "message": "Survey response submitted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Get survey results (Admin/Staff only)
@app.get("/api/surveys/{survey_id}/results")
async def get_survey_results(survey_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        survey = db.surveys.find_one({"_id": ObjectId(survey_id)})
        if not survey:
            raise HTTPException(status_code=404, detail="Survey not found")
        
        responses = list(db.survey_responses.find({"survey_id": survey_id}))
        
        return {
            "survey": convert_objectid_to_str(survey),
            "responses": convert_objectid_to_str(responses),
            "total_responses": len(responses)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Close survey (Admin/Staff only)
@app.put("/api/surveys/{survey_id}/close")
async def close_survey(survey_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        if user.get("role") not in ["admin", "staff"]:
            raise HTTPException(status_code=403, detail="Unauthorized")
        
        result = db.surveys.update_one(
            {"_id": ObjectId(survey_id)},
            {"$set": {"status": "closed", "closed_at": datetime.now().isoformat()}}
        )
        
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Survey not found")
        
        return {"success": True, "message": "Survey closed successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Delete survey (Admin only)
@app.delete("/api/surveys/{survey_id}")
async def delete_survey(survey_id: str, user: dict = Depends(get_current_user)):
    try:
        from bson import ObjectId
        
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Only admins can delete surveys")
        
        # Delete survey
        result = db.surveys.delete_one({"_id": ObjectId(survey_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Survey not found")
        
        # Delete all responses
        db.survey_responses.delete_many({"survey_id": survey_id})
        
        return {"success": True, "message": "Survey deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Helper function to notify users about new survey
async def _notify_survey_available(survey: dict, survey_id: str):
    """Notify target audience when a new survey is available"""
    try:
        target_audience = survey.get('target_audience', 'all')
        
        # Determine which users to notify
        users_to_notify = []
        
        if target_audience == 'all':
            users_to_notify = list(db.users.find({}))
        elif target_audience == 'students':
            users_to_notify = list(db.users.find({"role": "student"}))
        elif target_audience == 'staff':
            users_to_notify = list(db.users.find({"role": "staff"}))
        
        # Create notifications
        for user in users_to_notify:
            notification = {
                "user_email": user["email"],
                "type": "survey",
                "title": "📋 New Survey Available",
                "message": f"{survey.get('title')} - Please share your feedback!",
                "priority": "normal",
                "related_id": survey_id,
                "link": f"/{user.get('role', 'student')}_home",
                "status": "unread",
                "created_at": datetime.now().isoformat()
            }
            db.notifications.insert_one(notification)
        
        print(f"[INFO] Notified {len(users_to_notify)} users about new survey {survey_id}")
    except Exception as e:
        print(f"[ERROR] Failed to notify users about new survey: {e}")



