from __future__ import annotations

import json
import logging
from typing import Any, Dict

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.config import settings
from app.data import get_campus_map
from app.services.llm_followups import build_llm_style_followups

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


@router.post("/chat_question")
async def chat_question(question: str = Form(...), mode: str = Form("uni")):
    from rag_pipeline import get_answer

    normalized_mode = _normalize_mode(mode)

    answer, _ = get_answer(question, mode=normalized_mode)

    chips, suggest_live_chat, fu_source = build_llm_style_followups(
        user_question=question,
        answer_text=answer or "",
        k=4,
        mode=normalized_mode,
    )

    if suggest_live_chat:
        chips = [
            {"label": "Talk to an admin", "payload": {"type": "action", "action": "escalate"}}
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
async def chat_question_stream(question: str = Form(...), mode: str = Form("uni")):
    from rag_pipeline import get_answer_stream

    normalized_mode = _normalize_mode(mode)

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

        if suggest_live_chat:
            chips = [
                {"label": "Talk to an admin", "payload": {"type": "action", "action": "escalate"}}
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
        description_match = re.search(r"DESCRIPTION:\s*(.+)", answer, re.DOTALL)

        subject = subject_match.group(1).strip() if subject_match else "Support Request"
        category = category_match.group(1).strip() if category_match else "Other"
        priority = priority_match.group(1).strip() if priority_match else "Medium"
        description = description_match.group(1).strip() if description_match else request.message

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
        raise HTTPException(status_code=500, detail="Failed to analyze map request")


class RoutingRequest(BaseModel):
    message: str


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
        raise HTTPException(status_code=500, detail="Failed to analyze routing request")
