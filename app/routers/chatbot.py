from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.data import get_campus_map
from app.services.llm_followups import build_llm_style_followups
from app.services.student_learning import answer_from_student_scope

router = APIRouter()


ALLOWED_MODES = {"uni", "learning"}


def _normalize_mode(raw: str | None) -> str:
    value = (raw or "uni").strip().lower()
    if value in ALLOWED_MODES:
        return value
    if value in {"university", "campus"}:
        return "uni"
    if value in {"my_learning", "mylearning", "courses", "learning"}:
        return "learning"
    return "uni"


def _maybe_add_map_followup(question: str, chips: list[dict[str, Any]]) -> None:
    """
    Inspect the user's question to determine if it references a campus location or
    a request for directions. When a location can be resolved, append
    follow‑up action chips that allow the student to explore the location,
    get directions, or view the walking map.

    This helper is intentionally liberal in matching: it attempts to resolve the
    question to a known campus building even when explicit map keywords are not
    present. The goal is to surface map functionality for queries such as
    "Where is the engineering department?", "library hours", or simply the
    name of a building. If a building is recognized, multiple follow‑ups are
    provided:

      • ℹ️ About {location.name} – shows basic information about the location
        (description, hours, category).
      • 🧭 Get Directions – opens the directions flow (origin→destination)
        using the student's current location as the origin.
      • 🗺️ Show Map – opens the 3D map modal centered on the location.

    The frontend (chat.html) should interpret these payloads in onFollowupClick().
    """
    try:
        campus_map = get_campus_map(settings.campus_map_variant)
        # Attempt to resolve the question to a campus location. This will return
        # None if no matching alias or building name is found.
        location = campus_map.lookup(question)

        # If no match is found and the question clearly doesn't mention any
        # navigation keywords, bail out early. We still want to catch cases
        # where the question contains a building name but not the word "map".
        q_lower = (question or "").lower()
        if not location:
            map_keywords = (
                "map",
                "where is",
                "location",
                "building",
                "directions",
                "walk",
                "navigate",
                "route",
            )
            # If there are no keywords present and no location match, there is
            # nothing to suggest.
            if not any(k in q_lower for k in map_keywords):
                return
            # Try a fuzzy lookup by splitting the question into words and
            # matching aliases. This increases recall for phrases like
            # "how do I get to the nrc". The campus_map.iter_aliases() yields
            # (alias, location) pairs for all known buildings.
            for alias, loc in campus_map.iter_aliases():
                if alias.lower() in q_lower:
                    location = loc
                    break
            if not location:
                return

        # Destination string used by Google Maps / directions API.
        destination = f"{location.name}, Texas A&M University-Corpus Christi"

        # Avoid duplicate suggestions by checking existing chip payloads.
        def _already_exists(action: str) -> bool:
            for chip in chips:
                payload = chip.get("payload", {})
                if payload.get("action") == action and payload.get("destination") == destination:
                    return True
            return False

        # ℹ️ Information chip
        if not _already_exists("show_location_info"):
            chips.append(
                {
                    "label": f"ℹ️ About {location.name}",
                    "payload": {
                        "type": "action",
                        "action": "show_location_info",
                        "destination": destination,
                    },
                }
            )

        # 🧭 Directions chip (uses current location as origin)
        if not _already_exists("show_directions"):
            chips.append(
                {
                    "label": "🧭 Get Directions",
                    "payload": {
                        "type": "action",
                        "action": "show_directions",
                        "destination": destination,
                    },
                }
            )

        # 🗺️ Map chip
        if not _already_exists("show_map"):
            chips.append(
                {
                    "label": "🗺️ Show Map",
                    "payload": {
                        "type": "action",
                        "action": "show_map",
                        "destination": destination,
                    },
                }
            )
    except Exception as exc:
        logging.warning("Failed to add map followup: %s", exc)


@router.post("/chat_question")
async def chat_question(request: Request, question: str = Form(...), mode: str = Form("uni")):
    """
    Respond to chat questions from the student.

    For learning mode, delegate to the student learning assistant to handle course‑specific
    queries (listing courses, materials, quizzes, flashcards, summaries, etc.).
    Otherwise, use the generic RAG pipeline to answer campus questions.
    """
    from rag_pipeline import get_answer

    normalized_mode = _normalize_mode(mode)

    # If the question is in learning mode, use the student learning assistant
    if normalized_mode == "learning":
        user = request.session.get("user") or {}
        email = user.get("email")
        resp_obj = await answer_from_student_scope(request, question, email)
        answer = resp_obj.get("answer", "")
        chips = resp_obj.get("suggested_followups", [])
        suggest_live_chat = resp_obj.get("suggest_live_chat", False)
        # Note: map followups are not relevant for learning mode
        return {
            "answer": answer,
            "suggest_live_chat": suggest_live_chat,
            "suggested_followups": chips,
            "mode": normalized_mode,
        }

    # University mode: use the standard RAG pipeline
    answer, _ = get_answer(question, mode=normalized_mode)

    chips, suggest_live_chat, fu_source = build_llm_style_followups(
        user_question=question,
        answer_text=answer or "",
        k=4,
        mode=normalized_mode,
    )

    if chips is None:
        chips = []
    _maybe_add_map_followup(question, chips)

    if suggest_live_chat:
        chips = [
            {"label": "Talk to an admin", "payload": {
                "type": "action", "action": "escalate"}}
        ]

    resp: Dict[str, Any] = {
        "answer": answer,
        "suggest_live_chat": suggest_live_chat,
        "suggested_followups": chips,
        "mode": normalized_mode,
    }
    if settings.debug_followups:
        resp["followup_generator"] = fu_source
    return resp


@router.post("/chat_question_stream")
async def chat_question_stream(request: Request, question: str = Form(...), mode: str = Form("uni")):
    """
    Stream chatbot responses.  For learning mode, a single answer is returned without streaming.
    For university mode, responses are streamed using the RAG pipeline.
    """
    from rag_pipeline import get_answer_stream

    normalized_mode = _normalize_mode(mode)

    # If learning mode, produce a single SSE event with the full answer and followups
    if normalized_mode == "learning":
        user = request.session.get("user") or {}
        email = user.get("email")
        resp_obj = await answer_from_student_scope(request, question, email)
        answer = resp_obj.get("answer", "")
        chips = resp_obj.get("suggested_followups", [])
        suggest_live_chat = resp_obj.get("suggest_live_chat", False)

        async def simple_stream():
            # Send the answer as a single chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': answer})}\n\n"
            # Send followups
            followup_data: Dict[str, Any] = {
                "type": "followups",
                "suggest_live_chat": suggest_live_chat,
                "suggested_followups": chips,
                "mode": normalized_mode,
            }
            yield f"data: {json.dumps(followup_data)}\n\n"
            # Done
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            simple_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # Otherwise, stream using the RAG pipeline (university mode)
    async def event_generator():
        full_answer = ""
        for chunk in get_answer_stream(question, mode=normalized_mode):
            full_answer += chunk
            yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

        chips, suggest_live_chat, fu_source = build_llm_style_followups(
            user_question=question,
            answer_text=full_answer or "",
            k=4,
            mode=normalized_mode,
        )
        if chips is None:
            chips = []
        # Add map followup suggestions
        _maybe_add_map_followup(question, chips)
        if suggest_live_chat:
            chips = [
                {"label": "Talk to an admin", "payload": {
                    "type": "action", "action": "escalate"}}
            ]
        followup_data: Dict[str, Any] = {
            "type": "followups",
            "suggest_live_chat": suggest_live_chat,
            "suggested_followups": chips,
            "mode": normalized_mode,
        }
        if settings.debug_followups:
            followup_data["followup_generator"] = fu_source
        yield f"data: {json.dumps(followup_data)}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class TicketAnalysisRequest(BaseModel):
    message: str


@router.post("/api/analyze_ticket")
async def analyze_ticket_request(request: TicketAnalysisRequest):
    try:
        from rag_pipeline import get_answer
        import re

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

        answer, _ = get_answer(analysis_prompt)

        subject_match = re.search(r"SUBJECT:\s*(.+)", answer)
        category_match = re.search(r"CATEGORY:\s*(.+)", answer)
        priority_match = re.search(r"PRIORITY:\s*(.+)", answer)
        description_match = re.search(
            r"DESCRIPTION:\s*(.+)", answer, re.DOTALL)

        subject = subject_match.group(1).strip(
        ) if subject_match else "Support Request"
        category = category_match.group(
            1).strip() if category_match else "Other"
        priority = priority_match.group(
            1).strip() if priority_match else "Medium"
        description = description_match.group(
            1).strip() if description_match else request.message

        valid_categories = [
            "Technical Support",
            "Academic",
            "Financial",
            "Housing",
            "Registration",
            "Other",
        ]
        if category not in valid_categories:
            category = "Other"

        valid_priorities = ["Low", "Medium", "High"]
        if priority not in valid_priorities:
            priority = "Medium"

        return {
            "subject": subject[:100],
            "category": category,
            "priority": priority,
            "description": description,
        }
    except Exception as exc:
        print(f"Error analyzing ticket: {exc}")
        return {
            "subject": "Support Request",
            "category": "Other",
            "priority": "Medium",
            "description": request.message,
        }


class MapAnalysisRequest(BaseModel):
    message: str


@router.post("/api/analyze_map_request")
async def analyze_map_request(request: MapAnalysisRequest):
    try:
        campus_map = get_campus_map(settings.campus_map_variant)
        location = campus_map.lookup(request.message)

        if location:
            return {
                "variant": campus_map.variant,
                "location": location.to_response(),
                "description": f"📍 Here's the location of the **{location.name}**. {location.description}.",
            }

        return {
            "variant": campus_map.variant,
            "location": None,
            "description": f"Here's the {campus_map.description} showing all major buildings.",
        }

    except Exception as exc:
        logging.error(f"Error analyzing map request: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to analyze map request")


class RoutingRequest(BaseModel):
    message: str


class DirectionsRequest(BaseModel):
    """
    Request model used by /api/get_directions.

    The client sends a destination (location name) and optionally an origin name.
    If the origin is omitted, the user's current GPS location (obtained by the
    frontend) should be used. This endpoint resolves the requested locations to
    campus map coordinates so that the frontend can build a Google Maps
    directions request.
    """
    destination: str
    origin: str | None = None


@router.post("/api/get_directions")
async def get_directions(request: DirectionsRequest):
    """
    Resolve a destination (and optional origin) into campus map coordinates.

    Returns the variant of the campus map and the structured location data for
    the destination and origin. If the origin is not provided or cannot be
    resolved, ``origin`` will be null and the frontend should use the user's
    current GPS coordinates instead.
    """
    campus_map = get_campus_map(settings.campus_map_variant)
    dest_loc = campus_map.lookup(request.destination)
    origin_loc = None
    if request.origin:
        origin_loc = campus_map.lookup(request.origin)

    response: Dict[str, Any] = {
        "variant": campus_map.variant,
        "destination": dest_loc.to_response() if dest_loc else None,
        "origin": origin_loc.to_response() if origin_loc else None,
        "found": bool(dest_loc),
    }
    if not dest_loc:
        response["message"] = "I couldn't find the destination building."
    return response


# ===== New API Endpoints for Enhanced Map Flow =====

class LocationInfoRequest(BaseModel):
    """
    Request model used by /api/get_location_info.

    The frontend will send the exact location name or alias that was selected from a
    follow‑up chip. This endpoint returns rich metadata about the location so the
    assistant can respond with a detailed description.
    """
    location: str


@router.post("/api/get_location_info")
async def get_location_info(request: LocationInfoRequest):
    """
    Return details about a campus location.

    The response includes the location's name, address, description, hours of
    operation (if available), category, and geographic coordinates. If the
    location cannot be resolved, ``found`` will be set to False and a generic
    message provided. This endpoint powers the "About …" follow‑up chip.
    """
    campus_map = get_campus_map(settings.campus_map_variant)
    location = campus_map.lookup(request.location)
    if not location:
        return {
            "found": False,
            "message": "I'm sorry, I couldn't find details about that location.",
        }
    data = location.to_response()
    return {
        "found": True,
        "name": data["name"],
        "address": data.get("address"),
        "description": data.get("description"),
        "hours": data.get("hours"),
        "category": data.get("category"),
        "coordinates": {
            "lat": data.get("lat"),
            "lng": data.get("lng"),
        },
    }


@router.post("/api/analyze_routing_request")
async def analyze_routing_request(request: RoutingRequest):
    try:
        campus_map = get_campus_map(settings.campus_map_variant)
        message = request.message.lower()

        routing_patterns = [
            ("from", "to"),
            ("between", "and"),
            ("get to", "from"),
        ]

        alias_catalog = list(campus_map.iter_aliases())

        def resolve_location(text: str):
            candidate = text.strip().lower()
            for alias, location in alias_catalog:
                if alias in candidate or candidate == alias:
                    return location
            return None

        origin = None
        destination = None

        for start_word, end_word in routing_patterns:
            if start_word in message and end_word in message:
                _, after_start = message.split(start_word, 1)
                origin_text, sep, tail = after_start.partition(end_word)
                if not sep:
                    continue

                origin_match = resolve_location(origin_text)
                dest_match = resolve_location(tail)

                if origin_match:
                    origin = origin_match.to_response()
                if dest_match:
                    destination = dest_match.to_response()

                if origin and destination:
                    break

        if origin and destination:
            return {
                "variant": campus_map.variant,
                "origin": origin,
                "destination": destination,
                "found": True,
            }
        return {
            "variant": campus_map.variant,
            "origin": origin,
            "destination": destination,
            "found": False,
            "message": "I couldn't identify both the origin and destination buildings. Please specify like 'directions from Library to UC' or 'how to get from NRC to Wellness Center'.",
        }

    except Exception as exc:
        logging.error(f"Error analyzing routing request: {exc}")
        raise HTTPException(
            status_code=500, detail="Failed to analyze routing request")
