"""
Services for My Learning mode, implementing student‑specific question handling.

This module provides a helper function `answer_from_student_scope` that inspects
student queries, determines intent (list courses, show materials, open a file,
generate a quiz, create flashcards, summarize, or answer a question using
course materials), and returns a structured response compatible with the
chatbot API.

It relies on MongoDB collections for registrations, courses, materials, and
material texts; and uses the `llm_complete` helper from `llm_followups` to
generate language‑model responses.  A simple overlap score is used to match
questions to material texts.

Note: This implementation is intentionally conservative: it returns plain
Markdown answers and does not attempt to parse JSON from the LLM.  Quiz and
flashcard generation will produce a human‑readable list rather than strict
JSON.  Additional parsing can be added if your model reliably outputs JSON.
"""

from __future__ import annotations

import re
import json
from typing import Any, Dict, List, Optional
from bson import ObjectId
from fastapi import Request

from app.db.mongo import db, registrations_collection, courses_collection
from app.services.llm_followups import llm_complete
from app.core.config import settings


def simple_score(text: str, query: str) -> int:
    """Return a simple overlap score between query words and text words."""
    if not text or not query:
        return 0
    qwords = set(w.lower() for w in re.split(r"\W+", query) if w)
    twords = set(w.lower() for w in re.split(r"\W+", text) if w)
    # count intersection size
    return len(qwords & twords)


def _create_response(answer: str, followups: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Helper to build a response object for the chatbot."""
    return {
        "answer": answer,
        "suggested_followups": followups if followups is not None else [],
        "suggest_live_chat": False,
    }


async def answer_from_student_scope(request: Request, question: str, student_email: Optional[str]) -> Dict[str, Any]:
    """Answer student questions within the learning scope.

    This helper attempts to detect various intents such as listing courses,
    showing course materials, opening a specific material, generating quizzes,
    flashcards, summaries, and answering general questions using course
    material context.  It returns a dict with keys ``answer``,
    ``suggested_followups``, and ``suggest_live_chat``.

    Args:
        request: The FastAPI request object (to access session state).
        question: The student's input string.
        student_email: The student's email, or None if not logged in.

    Returns:
        A response dict compatible with the chatbot API.
    """
    if not student_email:
        return _create_response("I couldn’t find your student account. Please log in again.")

    qlow = (question or "").lower()

    # 1. Show stored quiz answers if requested
    if any(k in qlow for k in ["show answer", "what are the answers", "reveal solution", "give answers"]):
        last_quiz = request.session.get("last_quiz")
        if not last_quiz:
            return _create_response("I haven't given you a quiz yet. Ask me to 'generate a quiz' first!")
        formatted_answers = ["Here are the answers and explanations for the last quiz:"]
        for i, q in enumerate(last_quiz.get("quiz", []), 1):
            formatted_answers.append(f"\n**{i}. {q.get('question')}**")
            formatted_answers.append(f"   **Answer:** {q.get('answer')}")
            formatted_answers.append(f"   **Explanation:** {q.get('explanation', 'No explanation provided.')}")
        request.session.pop("last_quiz", None)
        return _create_response("\n".join(formatted_answers))

    # 2. Identify student's courses
    regs = list(registrations_collection.find({"student_email": student_email}))
    if not regs:
        return _create_response("You don’t have any registered courses right now.")
    course_ids = [ObjectId(r["course_id"]) for r in regs]
    student_courses = list(courses_collection.find({"_id": {"$in": course_ids}}))

    # 3. List courses intent
    if "list" in qlow and "course" in qlow:
        lines = ["Here are your registered courses:"]
        for c in student_courses:
            lines.append(f"- {c.get('title')} ({c.get('details')}) • {c.get('term')}")
        return _create_response("\n".join(lines))

    # 4. Attempt to match course by name, details, or code
    matched_course: Optional[Dict[str, Any]] = None
    for c in student_courses:
        title = (c.get("title") or "").lower()
        details = (c.get("details") or "").lower()
        code = details.split(",")[0].strip() if details else ""
        if (title and title in qlow) or (code and code.lower() in qlow):
            matched_course = c
            break

    # If no explicit course match, but only one course has text materials, use it
    if not matched_course:
        texts_for_student = list(db.course_materials_text.find({"course_id": {"$in": course_ids}}))
        course_ids_with_text = {t["course_id"] for t in texts_for_student}
        if len(course_ids_with_text) == 1:
            only_cid = list(course_ids_with_text)[0]
            matched_course = next((c for c in student_courses if c["_id"] == only_cid), None)

    if not matched_course:
        return _create_response("You’re in student mode. To ask about course content, please include the course name or code (e.g. 'explain topic from Human-Computer Interaction').")

    # 5. Get materials and text for matched course
    course_id = matched_course["_id"]
    course_title = matched_course.get("title")
    course_details = matched_course.get("details")
    course_term = matched_course.get("term")
    staff_emails = matched_course.get("staff_emails") or []
    staff_line = f"Instructor(s): {', '.join(staff_emails)}" if staff_emails else "Instructor not listed."
    base_line = (
        f"{matched_course.get('title')} ({matched_course.get('details')}) "
        f"Term: {matched_course.get('term')} "
        f"Schedule type: {matched_course.get('schedule_type')} "
        f"{staff_line}"
    )

    texts = list(db.course_materials_text.find({"course_id": course_id}))

    # 6. Show materials if the question explicitly asks for them
    if any(k in qlow for k in ["show materials", "list materials", "materials for"]):
        # Return all materials with titles and links
        mats = list(db.course_materials.find({"course_id": course_id, "visible": True}))
        if not mats:
            return _create_response(base_line + "\n\nNo materials have been uploaded for this course yet.")
        lines = [f"Materials for **{course_title}**:"]
        for m in mats:
            title = m.get("title") or m.get("file_name") or "Untitled"
            # determine link
            file_url = None
            if m.get("file_url"):
                file_url = m["file_url"]
            elif m.get("file_name"):
                file_url = f"/static/uploads/materials/{m['file_name']}"
            elif m.get("external_url"):
                file_url = m["external_url"]
            link = f"[{title}]({file_url})" if file_url else title
            lines.append(f"- {link}: {m.get('description') or ''}")
        return _create_response("\n".join(lines))

    # 7. Open a specific material file by title or file name
    if any(k in qlow for k in ["open", "download", "view"]):
        # Try to identify the material by matching words in title or file name
        mats = list(db.course_materials.find({"course_id": course_id, "visible": True}))
        target = None
        for m in mats:
            name = (m.get("title") or "").lower()
            fname = (m.get("file_name") or "").lower()
            # If the query mentions the material title or file name, select it
            if name and name in qlow:
                target = m
                break
            if fname and fname in qlow:
                target = m
                break
        # If no explicit match and only one material exists, pick it
        if not target and len(mats) == 1:
            target = mats[0]
        if target:
            file_url = None
            if target.get("file_url"):
                file_url = target["file_url"]
            elif target.get("file_name"):
                file_url = f"/static/uploads/materials/{target['file_name']}"
            elif target.get("external_url"):
                file_url = target["external_url"]
            if file_url:
                # Include course and material context in the message
                return _create_response(f"Here is your material from **{course_title}** – **{target.get('title') or target.get('file_name')}**: [{target.get('title') or target.get('file_name')}]({file_url})")
            else:
                return _create_response("Sorry, I couldn't find a link for that material.")

    # 8. If there are no text documents, list materials and return
    if not texts:
        mats = list(db.course_materials.find({"course_id": course_id, "visible": True}))
        if not mats:
            return _create_response(base_line + "\n\nNo materials have been uploaded for this course yet.")
        links = []
        for m in mats:
            title = m.get("title") or m.get("file_name") or "Material"
            file_url = None
            if m.get("file_url"):
                file_url = m["file_url"]
            elif m.get("file_name"):
                file_url = f"/static/uploads/materials/{m['file_name']}"
            elif m.get("external_url"):
                file_url = m["external_url"]
            links.append(f"- {title}: {file_url}")
        return _create_response(
            base_line
            + "\n\nI found material(s), but they are not text‑indexed, so I can't answer questions about them yet:\n"
            + "\n".join(links)
        )

    # 9. If the question mentions a specific material by title/description, return info and link
    for m in texts:
        title = (m.get("title") or "").lower()
        desc = (m.get("description") or "").lower()
        if (title and title in qlow) or (desc and desc in qlow):
            file_url = m.get("file_url")
            link_part = f"Link: {file_url}" if file_url else ""
            return _create_response(
                f"{m.get('title', 'Material')}: {m.get('description', '')} {link_part}".strip()
            )

    # 10. Choose the best text document based on overlap
    best_text_doc = None
    best_score = -1
    for t in texts:
        s = simple_score(t.get("text", ""), qlow)
        if s > best_score:
            best_text_doc = t
            best_score = s

    if not best_text_doc:
        return _create_response(base_line + "\n\nI found this course, but couldn't match your question to any specific material.")

    context = (best_text_doc.get("text") or "")[:8000]

    # 11. Detect quiz, flashcard, or summary intents
    is_quiz_request = any(k in qlow for k in ["quiz", "test me", "mcq", "generate questions", "practice problem"])
    is_flashcard_request = any(k in qlow for k in ["flashcard", "make flashcards", "key terms"])
    is_summary_request = any(k in qlow for k in ["summarize", "summary", "tl;dr", "give me the gist"])

    # Count numeric value for number of items
    def extract_number(q: str) -> int:
        m = re.search(r"(\d+)\s*", q)
        return int(m.group(1)) if m else 3

    try:
        # Determine material name and course title for context strings
        material_name = None
        if best_text_doc:
            # Use material title if available; otherwise fallback to file_name
            material_name = best_text_doc.get("title") or best_text_doc.get("file_name")
        course_info_line = f" (Source: {course_title} – {material_name})" if material_name else f" (Course: {course_title})"

        if is_quiz_request:
            num_questions = extract_number(qlow)
            # System prompt instructs the model to generate MCQs
            system_prompt = (
                f"You are a teaching assistant. Based on the provided course material, generate {num_questions} multiple‑choice quiz questions. "
                "Each question must have 4 options and a brief explanation for the correct answer."
            )
            user_prompt = f"Course Material:\n{context}"
            # Generate questions via llm_complete
            raw = llm_complete(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                model=settings.followup_model,
                temperature=0.3,
                max_tokens=2000,
            )
            # Attempt to parse JSON if present, otherwise return raw text
            try:
                quiz_data = json.loads(raw)
                request.session["last_quiz"] = quiz_data
                formatted_questions = [f"Okay, I've generated {len(quiz_data.get('quiz', []))} practice questions. {course_info_line}"]
                for i, q in enumerate(quiz_data.get("quiz", []), 1):
                    formatted_questions.append(f"\n**{i}. {q.get('question')}**")
                    for opt in q.get("options", []):
                        formatted_questions.append(f"   - {opt}")
                followups = [
                    {"label": "Show me the answers", "payload": {"type": "faq", "query": "show me the answers"}}
                ]
                return _create_response("\n".join(formatted_questions), followups)
            except Exception:
                # Store raw text for answer retrieval by stripping markdown
                request.session["last_quiz"] = {"quiz": []}
                return _create_response(f"Here are your quiz questions{course_info_line}:\n\n" + raw)

        if is_flashcard_request:
            num_cards = extract_number(qlow)
            system_prompt = (
                f"You are a teaching assistant. Based on the provided material, generate {num_cards} key terms and their definitions as flashcards. "
                "Return them as a list of 'Term: Definition' lines."
            )
            user_prompt = f"Course Material:\n{context}"
            raw = llm_complete(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                model=settings.followup_model,
                temperature=0.3,
                max_tokens=2000,
            )
            return _create_response(f"Here are your flashcards{course_info_line}:\n\n" + raw)

        if is_summary_request:
            system_prompt = "You are a teaching assistant. Summarize the provided course material in a few key bullet points."
            user_prompt = f"Course Material:\n{context}"
            summary = llm_complete(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                model=settings.followup_model,
                temperature=0.2,
                max_tokens=600,
            )
            return _create_response(f"Here is a summary of the material{course_info_line}:\n\n" + summary)

        # Standard question: answer from context
        system_prompt = (
            "You are a helpful and clever course assistant.\n"
            "1. Ground your answer strictly in the provided course material.\n"
            "2. You can (and should) rephrase, summarize, and explain concepts in a helpful, conversational way.\n"
            "3. If the user asks for an example, or if an example would help explain, create a simple, clear example relevant to the topic.\n"
            "4. If the question is completely unrelated to the material, state that you can only answer questions about that course's content."
        )
        user_prompt = f"Question: {question}\n\nCourse material:\n{context}"
        answer = llm_complete(
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            model=settings.followup_model,
            temperature=0.4,
            max_tokens=800,
        )
        # Provide follow‑up suggestions
        followups = [
            {"label": "Quiz me on this topic", "payload": {"type": "faq", "query": f"generate quiz on {question}"}},
            {"label": "Make flashcards for this", "payload": {"type": "faq", "query": f"make flashcards for {question}"}},
            {"label": "Summarize this topic", "payload": {"type": "faq", "query": f"summarize {question}"}},
        ]
        # Prepend course and material context to the answer
        answer_with_source = f"According to {course_title} – {material_name}, {answer}" if material_name else answer
        return _create_response(answer_with_source, followups)
    except Exception as exc:
        # If anything goes wrong, return a generic error
        return _create_response(f"I ran into an error trying to process that: {exc}. Please try a different question.")