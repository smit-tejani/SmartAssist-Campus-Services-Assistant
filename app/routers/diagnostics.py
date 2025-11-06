from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.services.llm_followups import llm_complete

router = APIRouter()


@router.get("/diag/llm")
def diag_llm():
    try:
        if not settings.openai_api_key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY missing")
        text = llm_complete(
            messages=[{"role": "system", "content": "Return the word OK"}],
            model=settings.followup_model,
            temperature=0.0,
            max_tokens=4,
        )
        return {"ok": True, "model": settings.followup_model, "reply": text}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
