from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List, Dict
from pymongo import MongoClient
from datetime import datetime
import os, json, random

# ---------- env ----------
from dotenv import load_dotenv
load_dotenv()

print("[BOOT] USE_LLM_FOLLOWUPS=", os.getenv("USE_LLM_FOLLOWUPS", "1"),
      "MODEL=", os.getenv("FOLLOWUP_MODEL", "gpt-4o-mini"),
      "OPENAI_KEY_PRESENT=", bool(os.getenv("OPENAI_API_KEY")))

# ---------- app ----------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ---------- mongo ----------
client = MongoClient("mongodb+srv://Manny0715:Manmeet12345@cluster0.1pf6oxg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client.smartassist
users_collection = db.users
live_chat_collection = db.live_chat
live_chat_sessions = db.live_chat_sessions

# Ensure a text index exists for follow-ups (safe to call once)
try:
    db.articles.create_index([("title","text"), ("content","text"), ("category","text")])
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
    cur = (db.articles.find(
            {"$text": {"$search": query}},
            {"title": 1, "category": 1, "url": 1, "score": {"$meta": "textScore"}}
          )
          .sort([("score", {"$meta": "textScore"})])
          .limit(limit))
    return list(cur)

def _should_offer_live_chat(user_q: str, answer_text: str, hits: int) -> bool:
    if _wants_human(user_q):
        return True
    low_conf = [
        "i'm not sure", "no information", "could not find", "not available",
        "i don't have", "unable to find"
    ]
    a = (answer_text or "").lower()
    return hits == 0 or any(p in a for p in low_conf)

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

# ======================================================================================
#                                   WEBSOCKETS
# ======================================================================================
@app.websocket("/ws/student/{session_id}")
async def student_ws(websocket: WebSocket, session_id: str):
    await manager.connect_student(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_json()
            message_text = data.get("message", "")

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
                await manager.broadcast_admins({
                    "type": "queued_ping",
                    "session_id": session_id
                })

    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket):
    await manager.connect_admin(websocket)
    admin_id = str(id(websocket))

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

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

                await manager.send_to_student(session_id, {
                    "type": "status",
                    "session_id": session_id,
                    "status": "live"
                })
                await websocket.send_json({"type": "joined", "session_id": session_id})

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
        manager.disconnect(websocket)

# ======================================================================================
#                                   REST API
# ======================================================================================
@app.get("/api/chat/{session_id}")
async def get_chat_history(session_id: str):
    return list(live_chat_collection
                .find({"session_id": session_id}, {"_id": 0})
                .sort("created_at", 1))

@app.post("/api/chat/{session_id}/escalate")
async def escalate(session_id: str):
    # student has asked for an agent — surface to admins now
    live_chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$setOnInsert": {"session_id": session_id, "assigned_admin": None},
            "$set": {"status": "queued", "student_connected": True, "name": f"Student {session_id[:4]}"},
        },
        upsert=True
    )
    await manager.broadcast_admins({
        "type": "new_session",
        "session_id": session_id,
        "status": "queued",
        "name": f"Student {session_id[:4]}"
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
    for d in docs:
        if not d.get("name"):
            sid = d.get("session_id","")
            d["name"] = f"Student {sid[:4]}" if sid else "Student"
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
    role: str = Form(...)
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
        "password": password,  # TODO: hash
        "role": role
    })
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "message": "Registration successful! Please login."}
    )

@app.post("/login")
async def post_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    user = users_collection.find_one({"email": email})
    if user and user["password"] == password and user["role"] == role:
        if role == "student":
            return RedirectResponse("/student_home", status_code=302)
        elif role == "staff":
            return RedirectResponse("/staff_home", status_code=302)
        elif role == "admin":
            return RedirectResponse("/admin_home", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid credentials or role!"})

@app.get("/student_home", response_class=HTMLResponse)
async def student_dashboard(request: Request):
    return templates.TemplateResponse("student_home.html", {"request": request})

@app.get("/staff_home", response_class=HTMLResponse)
async def staff_dashboard(request: Request):
    return templates.TemplateResponse("staff_home.html", {"request": request})

@app.get("/admin_home", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin_home.html", {"request": request})

@app.get("/guest_home", response_class=HTMLResponse)
async def guest_dashboard(request: Request):
    return templates.TemplateResponse("guest_home.html", {"request": request})

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    return templates.TemplateResponse("chat.html", {"request": request})

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

    if suggest_live_chat:
        chips.append({"label": "Talk to an admin", "payload": {"type": "action", "action": "escalate"}})

    resp = {
        "answer": answer,
        "suggest_live_chat": suggest_live_chat,
        "suggested_followups": chips
    }
    if DEBUG_FOLLOWUPS:
        resp["followup_generator"] = fu_source  # "openai" | "fallback" | "fallback_error"
    return resp
