from fastapi import FastAPI, Request, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import List
from pymongo import MongoClient

# ------------------ FastAPI App ------------------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")  # folder for HTML templates

# ------------------ MongoDB Setup ------------------
client = MongoClient("mongodb+srv://Manny0715:Manmeet12345@cluster0.1pf6oxg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")  # Replace with your MongoDB URL if needed
db = client.smartassist
users_collection = db.users
live_chat_collection = db.live_chat

# ------------------ Connection Manager for Live Chat ------------------
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

        # If session doesn’t exist in DB, create it
        if not live_chat_collection.find_one({"session_id": session_id}):
            live_chat_collection.insert_one({
                "session_id": session_id,
                "sender": "system",
                "message": "New chat session started."
            })

        # Notify all admins about the new or reconnected session
        await self.broadcast_admins({
            "type": "new_session",
            "session_id": session_id,
            "name": f"Student {session_id[:4]}"
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.admins:
            self.admins.remove(websocket)
            print("❌ Admin disconnected")
        else:
            for sid, ws in list(self.students.items()):
                if ws == websocket:
                    del self.students[sid]
                    print(f"❌ Student disconnected: {sid}")

    async def send_to_student(self, session_id: str, message: dict):
        if session_id in self.students:
            await self.students[session_id].send_json(message)

    async def broadcast_admins(self, message: dict):
        for admin in self.admins:
            await admin.send_json(message)

manager = ChatManager()

# ------------------ WebSocket Endpoints ------------------
@app.websocket("/ws/student/{session_id}")
async def student_ws(websocket: WebSocket, session_id: str):
    await manager.connect_student(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_json()
            message_text = data.get("message")

            # Store student message
            live_chat_collection.insert_one({
                "session_id": session_id,
                "sender": "student",
                "message": message_text
            })

            # Broadcast to all admins
            await manager.broadcast_admins({
                "type": "message",
                "session_id": session_id,
                "sender": "student",
                "message": message_text
            })

    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket):
    await manager.connect_admin(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            if msg_type == "message":
                session_id = data.get("session_id")
                message_text = data.get("message")

                # Save admin message
                live_chat_collection.insert_one({
                    "session_id": session_id,
                    "sender": "admin",
                    "message": message_text
                })

                # Forward to that student
                await manager.send_to_student(session_id, {
                    "type": "message",
                    "session_id": session_id,
                    "sender": "admin",
                    "message": message_text
                })
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ------------------ REST API: Fetch Chat History ------------------
@app.get("/api/chat/{session_id}")
async def get_chat_history(session_id: str):
    messages = list(live_chat_collection.find({"session_id": session_id}, {"_id": 0}))
    return messages

# ------------------ Auth & Dashboards ------------------
@app.get("/", response_class=HTMLResponse)
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
        return templates.TemplateResponse("register.html", {"request": request, "error": "Passwords do not match!"})

    if users_collection.find_one({"email": email}):
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered!"})

    users_collection.insert_one({
        "full_name": full_name,
        "email": email,
        "password": password,
        "role": role
    })
    return templates.TemplateResponse("login.html", {"request": request, "message": "Registration successful! Please login."})

@app.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

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

@app.post("/chat_question")
async def chat_question(question: str = Form(...)):
    from rag_pipeline import get_answer  # Your RAG logic in a separate file
    answer, suggest_live_chat = get_answer(question)
    return {
        "answer": answer,
        "suggest_live_chat": suggest_live_chat
    }
