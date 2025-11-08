from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, WebSocket, WebSocketDisconnect

from app.db.mongo import live_chat_collection, live_chat_sessions
from app.services.live_chat import manager

router = APIRouter()


@router.websocket("/ws/student/{session_id}")
async def student_ws(websocket: WebSocket, session_id: str):
    print(f"[DEBUG] Student connected with session_id: {session_id}")
    await manager.connect_student(websocket, session_id)
    try:
        while True:
            data = await websocket.receive_json()
            message_text = data.get("message", "")
            print(f"[DEBUG] Received message from student: {message_text}")

            live_chat_collection.insert_one(
                {
                    "session_id": session_id,
                    "sender": "student",
                    "message": message_text,
                    "created_at": datetime.utcnow(),
                }
            )

            sess = live_chat_sessions.find_one({"session_id": session_id})
            if sess and sess.get("status") == "live":
                await manager.broadcast_admins(
                    {
                        "type": "message",
                        "session_id": session_id,
                        "sender": "student",
                        "message": message_text,
                    }
                )
            else:
                queued_sessions = list(live_chat_sessions.find({"status": "queued"}).sort("created_at", 1))
                queue_position = next(
                    (i + 1 for i, s in enumerate(queued_sessions) if s["session_id"] == session_id),
                    None,
                )

                await manager.broadcast_admins(
                    {
                        "type": "queued_ping",
                        "session_id": session_id,
                        "queue_position": queue_position,
                    }
                )

    except WebSocketDisconnect:
        print(f"[DEBUG] Student disconnected with session_id: {session_id}")
        manager.disconnect(websocket)


@router.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket):
    print("[DEBUG] /ws/admin endpoint accessed")
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
                if (
                    not sess
                    or not sess.get("student_connected")
                    or sess.get("status") == "closed"
                ):
                    await websocket.send_json(
                        {"type": "error", "reason": "Student not connected / session closed."}
                    )
                    await websocket.send_json({"type": "session_removed", "session_id": session_id})
                    continue

                res = live_chat_sessions.update_one(
                    {"session_id": session_id, "status": {"$in": ["queued", "live"]}},
                    {"$set": {"status": "live", "assigned_admin": admin_id}},
                )
                if res.matched_count == 0:
                    await websocket.send_json({"type": "error", "reason": "Session not found or closed."})
                    continue

                sess = live_chat_sessions.find_one({"session_id": session_id})

                await manager.send_to_student(
                    session_id,
                    {
                        "type": "status",
                        "session_id": session_id,
                        "status": "live",
                    },
                )
                await websocket.send_json(
                    {
                        "type": "joined",
                        "session_id": session_id,
                        "student_name": sess.get("student_name"),
                        "student_email": sess.get("student_email"),
                    }
                )

            elif msg_type == "message":
                session_id = data.get("session_id")
                message_text = data.get("message", "")

                sess = live_chat_sessions.find_one({"session_id": session_id})
                if (
                    not sess
                    or sess.get("status") != "live"
                    or sess.get("assigned_admin") != admin_id
                ):
                    await websocket.send_json(
                        {"type": "error", "reason": "Session not live or not assigned to you."}
                    )
                    continue

                live_chat_collection.insert_one(
                    {
                        "session_id": session_id,
                        "sender": "admin",
                        "message": message_text,
                        "created_at": datetime.utcnow(),
                    }
                )
                await manager.send_to_student(
                    session_id,
                    {
                        "type": "message",
                        "session_id": session_id,
                        "sender": "admin",
                        "message": message_text,
                    },
                )

            else:
                await websocket.send_json({"type": "error", "reason": "Unknown message type."})

    except WebSocketDisconnect:
        print("[DEBUG] Admin disconnected")
        manager.disconnect(websocket)


@router.get("/api/chat/{session_id}")
async def get_chat_history(session_id: str):
    print(f"[DEBUG] Fetching chat history for session_id: {session_id}")
    messages = list(
        live_chat_collection.find({"session_id": session_id}, {"_id": 0}).sort("created_at", 1)
    )
    print(f"[DEBUG] Retrieved messages: {messages}")
    return messages


@router.post("/api/chat/{session_id}/escalate")
async def escalate(session_id: str, student_info: dict = Body(default={})):  # noqa: B008
    student_name = student_info.get("student_name", f"Student {session_id[:4]}")
    student_email = student_info.get("student_email")

    live_chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$setOnInsert": {
                "session_id": session_id,
                "assigned_admin": None,
                "created_at": datetime.utcnow(),
            },
            "$set": {
                "status": "queued",
                "student_connected": True,
                "student_name": student_name,
                "student_email": student_email,
                "name": student_name,
            },
        },
        upsert=True,
    )
    await manager.broadcast_admins(
        {
            "type": "new_session",
            "session_id": session_id,
            "status": "queued",
            "student_name": student_name,
            "student_email": student_email,
            "name": student_name,
        }
    )
    return {"ok": True}


@router.post("/api/chat/{session_id}/end")
async def end_chat(session_id: str):
    live_chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "status": "closed",
                "student_connected": False,
                "assigned_admin": None,
                "ended_at": datetime.utcnow(),
            }
        },
    )
    await manager.broadcast_admins({"type": "session_removed", "session_id": session_id})
    return {"ok": True}


@router.get("/api/admin/live_chats")
async def list_live_chats():
    docs = list(live_chat_sessions.find({}, {"_id": 0}))
    order = {"queued": 0, "live": 1, "closed": 2}
    docs.sort(key=lambda x: order.get(x.get("status", "queued"), 9))
    return docs
