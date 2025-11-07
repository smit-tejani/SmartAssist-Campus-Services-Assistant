# app/services/live_chat.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, List

import anyio
from fastapi import WebSocket

from app.db.mongo import live_chat_collection, live_chat_sessions


class ChatManager:
    """
    Manages live chat WebSocket connections for admins and students.

    Public API (kept stable so routers keep working):
      - connect_admin(websocket)
      - disconnect_admin(websocket)
      - connect_student(websocket, session_id)
      - disconnect_student(session_id)
      - send_to_student(session_id, message)
      - broadcast_admins(message)
      - save_message(session_id, sender, message)  # persists chat messages
    """

    def __init__(self) -> None:
        # active admin websockets
        self.admins: List[WebSocket] = []
        # map session_id -> student websocket
        self.students: Dict[str, WebSocket] = {}

        # small async lock guarding mutations of admins/students
        self._lock = anyio.Lock()

    # -------------------------
    # Connection lifecycle
    # -------------------------
    async def connect_admin(self, websocket: WebSocket) -> None:
        """Accept and register a new admin websocket."""
        await websocket.accept()
        async with self._lock:
            self.admins.append(websocket)
        print("✅ Admin connected")

    async def disconnect_admin(self, websocket: WebSocket) -> None:
        """Remove an admin websocket if present."""
        async with self._lock:
            try:
                self.admins.remove(websocket)
                print("⛔ Admin disconnected")
            except ValueError:
                # already removed or never present
                pass

    async def connect_student(self, websocket: WebSocket, session_id: str) -> None:
        """
        Accept and register a student websocket for a given session_id.
        Persist or update session metadata in `live_chat_sessions`.
        """
        await websocket.accept()
        async with self._lock:
            self.students[session_id] = websocket

        # Upsert session metadata in DB
        try:
            live_chat_sessions.update_one(
                {"session_id": session_id},
                {
                    "$set": {
                        "session_id": session_id,
                        "connected": True,
                        "last_seen": datetime.utcnow().isoformat(),
                    }
                },
                upsert=True,
            )
        except Exception as exc:
            # DB error should not break connection
            print(
                f"[ERROR] live_chat: connect_student DB update failed: {exc}")

        print(f"✅ Student connected: {session_id}")

    async def disconnect_student(self, session_id: str) -> None:
        """Remove student websocket mapping and mark session disconnected in DB."""
        async with self._lock:
            ws = self.students.pop(session_id, None)

        try:
            live_chat_sessions.update_one(
                {"session_id": session_id},
                {"$set": {"connected": False, "last_seen": datetime.utcnow().isoformat()}},
                upsert=True,
            )
        except Exception as exc:
            print(
                f"[ERROR] live_chat: disconnect_student DB update failed: {exc}")

        print(f"❌ Student disconnected: {session_id}")

    # -------------------------
    # Messaging helpers
    # -------------------------
    async def send_to_student(self, session_id: str, message: dict) -> None:
        """Send a JSON message to the student WebSocket if connected."""
        ws = None
        async with self._lock:
            ws = self.students.get(session_id)

        if ws is None:
            # No active connection for this session
            print(
                f"[WARN] send_to_student: no websocket for session {session_id}")
            return

        try:
            await ws.send_json(message)
        except Exception as exc:
            # If sending fails, remove the socket mapping to avoid stale sockets
            async with self._lock:
                self.students.pop(session_id, None)
            print(f"[ERROR] send_to_student failed for {session_id}: {exc}")

    async def broadcast_admins(self, message: dict) -> None:
        """Send a JSON message to all connected admin sockets (best-effort)."""
        async with self._lock:
            admins_snapshot = list(self.admins)

        for admin in admins_snapshot:
            try:
                await admin.send_json(message)
            except Exception:
                # ignore single admin send failures
                try:
                    async with self._lock:
                        self.admins.remove(admin)
                except Exception:
                    pass

    # -------------------------
    # Persistence
    # -------------------------
    def save_message(self, session_id: str, sender: str, message: str) -> str:
        """
        Persist a chat message to live_chat_collection.
        Returns the inserted document id (string) on success.
        This method is synchronous (DB driver can be sync). Keep it simple.
        """
        try:
            doc = {
                "session_id": session_id,
                "sender": sender,
                "message": message,
                "timestamp": datetime.utcnow().isoformat(),
            }
            result = live_chat_collection.insert_one(doc)
            return str(result.inserted_id)
        except Exception as exc:
            print(f"[ERROR] save_message: {exc}")
            # In case of DB problem, return empty string to signify failure
            return ""

    # -------------------------
    # Utility / admin helpers
    # -------------------------
    async def list_active_sessions(self) -> List[str]:
        """Return a snapshot list of currently connected student session ids."""
        async with self._lock:
            return list(self.students.keys())


# module-level singleton preserved for backwards compat
manager = ChatManager()
