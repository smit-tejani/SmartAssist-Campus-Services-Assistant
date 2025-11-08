from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Dict, List

from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.mongo import kb_collection, courses_collection


ESCALATION_KEYWORDS = {
    "agent",
    "human",
    "person",
    "representative",
    "talk to someone",
    "talk to admin",
    "live chat",
    "connect me",
    "escalate",
    "call",
    "phone",
    "help desk",
    "support",
}


def llm_complete(messages, model="gpt-4o-mini", temperature=0.4, max_tokens=180) -> str:
    try:
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content.strip()
    except Exception as v1_err:  # pragma: no cover - network call
        import openai

        if not getattr(openai, "api_key", None):
            openai.api_key = settings.openai_api_key
        legacy_model = os.getenv("FOLLOWUP_MODEL_LEGACY", "gpt-3.5-turbo")
        try:
            resp = openai.ChatCompletion.create(
                model=legacy_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return resp["choices"][0]["message"]["content"].strip()
        except Exception as v0_err:  # pragma: no cover - network call
            raise RuntimeError(f"OpenAI failed (v1: {v1_err!r}; v0: {v0_err!r})")


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


def _course_text_search(query: str, limit: int = 8) -> List[Dict]:
    q = (query or "").strip().lower()
    if not q:
        return []

    tokens = [t for t in re.split(r"\W+", q) if t]
    if not tokens:
        return []

    results: List[Dict] = []
    for doc in courses_collection.find({}, {"title": 1, "details": 1, "term": 1}):
        haystack = " ".join(
            str(doc.get(field, "")) for field in ("title", "details", "term")
        ).lower()
        if not haystack:
            continue

        if all(token in haystack for token in tokens) or any(token in haystack for token in tokens[-2:]):
            results.append(doc)
        if len(results) >= limit:
            break

    return results


def _should_offer_live_chat(user_q: str, answer_text: str, hits: int) -> bool:
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
    return any(p in a for p in low_conf)


def _safe_json_list(s: str) -> List[str]:
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
    except Exception:
        pass

    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        frag = m.group(0)
        try:
            data = json.loads(frag)
            if isinstance(data, list):
                return [str(x) for x in data if isinstance(x, str)]
        except Exception:
            return []
    return []


def _llm_generate_followups(user_q: str, answer_text: str, candidates: List[Dict], k: int = 4) -> List[str]:
    ctx_lines = []
    for c in candidates[:10]:
        t = (c.get("title") or "").strip()
        u = c.get("url", "")
        cat = c.get("category", "")
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
        messages=[{"role": "system", "content": sys}, {"role": "user", "content": usr}],
        model=settings.followup_model,
        temperature=0.4,
        max_tokens=180,
    )
    items = _safe_json_list(text)
    uniq, seen = [], set()
    for it in items:
        s = it.strip()
        if s.endswith("?"):
            s = s[:-1]
        if s and s.lower() not in seen:
            seen.add(s.lower())
            uniq.append(s)
        if len(uniq) >= k:
            break
    return uniq


def build_llm_style_followups(user_question: str, answer_text: str, k: int = 4, mode: str = "uni"):
    if mode == "learning":
        hits = _course_text_search(user_question, limit=8)
        if not hits and answer_text:
            hits = _course_text_search(answer_text, limit=8)
    else:
        hits = _mongo_text_search(user_question, limit=8)
        if not hits and answer_text:
            hits = _mongo_text_search(answer_text, limit=8)

    suggestions: List[str] = []
    source = "fallback"

    if settings.use_llm_followups and settings.openai_api_key and mode != "learning":
        try:
            suggestions = _llm_generate_followups(user_question, answer_text, hits, k=k)
            if suggestions:
                source = "openai"
        except Exception as exc:  # pragma: no cover - network call
            print("[LLM] followups error:", repr(exc))
            suggestions = []
            source = "fallback_error"

    if not suggestions:
        if mode == "learning":
            base = []
            for course in hits[:6]:
                title = (course.get("title") or "Course").strip()
                term = (course.get("term") or "").strip()
                detail = (course.get("details") or "").strip()
                label = title
                if term:
                    label += f" ({term})"
                if detail:
                    label += f" – {detail}"
                base.append(f"Show details for {label}")

            suggestions = [s for s in base if s][:k]

            if not suggestions:
                suggestions = [
                    "Show available courses this term",
                    "How many credit hours is Data Mining",
                    "What is the CRN for Machine Learning",
                    "List graduate courses in Fall 2025",
                ][:k]
        else:
            base = [h.get("title", "") for h in hits[:6] if h.get("title")]
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


__all__ = [
    "build_llm_style_followups",
    "llm_complete",
]
