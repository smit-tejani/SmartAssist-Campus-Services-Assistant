from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import anyio
from fastapi import WebSocket

from app.db.mongo import live_chat_collection, live_chat_sessions


class ChatManager:
    def __init__(self) -> None:
        self.admins: List[WebSocket] = []
        self.students: Dict[str, WebSocket] = {}

    async def connect_admin(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.admins.append(websocket)
        print("✅ Admin connected")

    async def connect_student(self, websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        self.students[session_id] = websocket
        print(f"✅ Student connected: {session_id}")

        live_chat_sessions.update_one(
            {"session_id": session_id},
            {
                "$setOnInsert": {
                    "session_id": session_id,
                    "status": "queued",
                    "assigned_admin": None,
                    "name": f"Student {session_id[:4]}",
                },
                "$set": {"student_connected": True},
            },
            upsert=True,
        )

        if not live_chat_collection.find_one({"session_id": session_id}):
            live_chat_collection.insert_one(
                {
                    "session_id": session_id,
                    "sender": "system",
                    "message": "New chat session started.",
                    "created_at": datetime.utcnow(),
                }
            )

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.admins:
            self.admins.remove(websocket)
            print("❌ Admin disconnected")
            return

        for sid, ws in list(self.students.items()):
            if ws == websocket:
                del self.students[sid]
                live_chat_sessions.update_one(
                    {"session_id": sid},
                    {"$set": {"student_connected": False, "status": "closed"}},
                )
                anyio.from_thread.run(self.broadcast_admins, {"type": "session_removed", "session_id": sid})
                print(f"❌ Student disconnected: {sid}")

    async def send_to_student(self, session_id: str, message: dict) -> None:
        if session_id in self.students:
            await self.students[session_id].send_json(message)

    async def broadcast_admins(self, message: dict) -> None:
        for admin in list(self.admins):
            try:
                await admin.send_json(message)
            except Exception:
                pass


manager = ChatManager()
