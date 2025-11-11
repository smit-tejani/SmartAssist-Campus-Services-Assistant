import os
import io
from typing import List, Optional, Tuple

import docx  # python-docx
import fitz  # PyMuPDF
import openai
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

# --- Configuration & Setup ---

openai.api_key = os.environ.get("OPENAI_API_KEY")
if not openai.api_key:
    print("WARNING: OPENAI_API_KEY not set. Assignment checker will fail if called.")

client = openai.OpenAI()

# --- Pydantic Models ---

class RequirementDetail(BaseModel):
    requirement: str = Field(
        description="The specific requirement that was checked."
    )
    passed: bool = Field(
        description="Whether the student's text met this requirement."
    )

# NEW: A universal item for all feedback (good or bad)
class FeedbackItem(BaseModel):
    feedback: str = Field(
        description="The detailed, constructive feedback."
    )
    snippet: Optional[str] = Field(
        description=(
            "The exact, verbatim text snippet from the document that this feedback "
            "refers to. This provides the 'location' of the feedback."
        )
    )

# UPDATED: Feedback model now uses FeedbackItem for all lists
class Feedback(BaseModel):
    to_fix: List[FeedbackItem] = Field(
        description=(
            "List of *completely wrong* or *off-topic* content."
        )
    )
    met: List[FeedbackItem] = Field(
        description="List of *high-quality* content that meets requirements."
    )
    notes: List[FeedbackItem] = Field(
        description="List of *low-quality* or *'needs improvement'* content."
    )

# UPDATED: Simplified the main response model
class AssignmentScoreResponse(BaseModel):
    score: int = Field(description="The final, professor-grade score from 0-100.")
    plagiarism: int = Field(
        description="Estimated plagiarism risk score (0-100)."
    )
    formatting: int = Field(
        description="Estimated formatting quality score (0-100)."
    )
    requirements_count: int = Field(
        description="Total number of professor requirements checked."
    )
    dominant_font_size: Optional[str] = Field(
        description="Dominant font size detected, e.g., '12pt'."
    )
    dominant_font_name: Optional[str] = Field(
        description="Dominant font name detected, e.g., 'Times New Roman'."
    )
    details: List[RequirementDetail] = Field(
        description="A list of checks for each individual requirement."
    )
    feedback: Feedback = Field(
        description="The detailed feedback bundle."
    )


class ServerResponse(AssignmentScoreResponse):
    """
    This is what our server returns to the frontend.
    """
    full_text: str = Field(
        description="The full, extracted text of the assignment."
    )


# --- FastAPI Router ---

router = APIRouter(
    prefix="/api/assignment-checker",
    tags=["Assignment Checker"],
)

# --- Text & Metadata Extraction ---


def extract_text_from_pdf(file_stream: bytes) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns: (text, dominant_font_size, dominant_font_name)
    """
    text = ""
    font_sizes = {}
    font_names = {}
    try:
        with fitz.open(stream=file_stream, filetype="pdf") as doc:
            for page in doc:
                text += page.get_text()
                blocks = page.get_text(
                    "dict", flags=fitz.TEXT_PRESERVE_WHITESPACE
                )["blocks"]
                for b in blocks:
                    if "lines" in b:
                        for l in b["lines"]:
                            for s in l["spans"]:
                                size = round(s["size"])
                                name = s["font"]
                                font_sizes[size] = font_sizes.get(size, 0) + 1
                                font_names[name] = font_names.get(name, 0) + 1

        dominant_size = (
            f"{max(font_sizes, key=font_sizes.get)}pt" if font_sizes else None
        )
        dominant_name = (
            max(font_names, key=font_names.get) if font_names else None
        )
        return text, dominant_size, dominant_name
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return "", None, None


def extract_text_from_docx(file_stream: io.BytesIO) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns: (text, dominant_font_size, dominant_font_name)
    """
    text = ""
    font_sizes = {}
    font_names = {}
    try:
        doc = docx.Document(file_stream)
        for para in doc.paragraphs:
            text += para.text + "\n"
            for run in para.runs:
                if run.font.size:
                    size = round(run.font.size.pt)
                    font_sizes[size] = font_sizes.get(size, 0) + 1
                if run.font.name:
                    name = run.font.name
                    font_names[name] = font_names.get(name, 0) + 1

        dominant_size = (
            f"{max(font_sizes, key=font_sizes.get)}pt" if font_sizes else None
        )
        dominant_name = (
            max(font_names, key=font_names.get) if font_names else None
        )
        return text, dominant_size, dominant_name
    except Exception as e:
        print(f"Error reading DOCX: {e}")
        return "", None, None


def extract_text_from_image(file_stream: bytes) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Placeholder – you can later wire in OCR here.
    """
    print("OCR function is placeholder.")
    return "OCR text extraction is not yet implemented.", None, None


# --- AI Agent Logic ---

def get_system_prompt() -> str:
    """
    System prompt that tells the model to act as a professor,
    judging not just mismatch but also content *quality* and
    providing snippets for ALL feedback.
    
    UPDATED: Added an *extremely strict* rule to define "off-topic"
    to prevent the AI from mis-categorizing content.
    """
    return f"""
You are an expert Professor grading an assignment. Your goal is to give a
realistic, nuanced score and constructive, high-quality feedback.

Your tasks:

1.  **Analyze Content & Requirements**:
    -   You will be given [PROFESSOR REQUIREMENTS].
    -   You will analyze the [ASSIGNMENT TEXT].

    -   **STRICT RULE**: Content is *only* "relevant" if it **directly
      addresses** a topic in the [PROFESSOR REQUIREMENTS].
    -   Content about *other* topics (e.g., "Human Factors," "typing
      tests," "HCI benchmarks," "language redundancy") is **100%
      off-topic**, even if it is well-written or in the same general
      field.

    -   Identify **off-topic/wrong** content. Put this in the `to_fix` list.
    -   Identify **high-quality, on-topic** content. Put this in the `met` list.
    -   Identify **relevant but weak/low-quality** content (e.g., it's
      about machine learning, but the explanation is superficial).
      Put this in the `notes` list.

2.  **Generate High-Quality Feedback (CRITICAL)**:
    -   For **EVERY** item in `to_fix`, `met`, and `notes`, you MUST
        provide:
        1.  `feedback`: Detailed, constructive advice. Explain *why*.
        2.  `snippet`: The exact, verbatim text snippet (the "location")
            *from the [ASSIGNMENT TEXT]* that the feedback refers to.
    
    -   **CRITICAL SNIPPET RULE**: Your `snippet` MUST be the
        **full, verbatim paragraph** from the text, not just one sentence.

3.  **Populate Requirement Checklist**:
    -   For *each* requirement in [PROFESSOR REQUIREMENTS], you MUST
        create a `RequirementDetail` object.
    -   Set `passed: true` if it's fully met.
    -   Set `passed: false` if it is missing or only partially met.

4.  **Estimate Nuanced Professor's Score**:
    -   Your `score` (0-100) must be realistic, based on the *quality*
      of the content, not just a count of met requirements.

You MUST respond in a single, valid JSON object adhering to this schema
(omit the `full_text` field – the server will add it):

SCHEMA:
{AssignmentScoreResponse.model_json_schema()}
"""

def create_user_prompt(requirements: str, metadata: dict, student_text: str) -> str:
    """
    Build the user message that includes requirements, metadata and the assignment text.
    """
    req_list = [r.strip() for r in requirements.split("\n") if r.strip()]
    formatted_reqs = "\n- ".join(req_list) if req_list else "(no explicit requirements)"

    return f"""
Here is the assignment review request. Please provide your analysis in the required JSON format.

[PROFESSOR REQUIREMENTS]
- Total Requirements: {len(req_list)}
- {formatted_reqs}

[METADATA]
- Dominant Font: {metadata.get('font_name', 'N/A')}
- Dominant Size: {metadata.get('font_size', 'N/A')}
- Word Count: {metadata.get('word_count', 'N/A')}

[ASSIGNMENT TEXT]
{student_text}
"""


# --- API Endpoint ---


@router.post("/score", response_model=ServerResponse)
async def check_assignment(
    requirements: str = Form(...),
    student_text: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
) -> ServerResponse:
    """
    Main scoring endpoint.
    """
    if not student_text and not file:
        raise HTTPException(
            status_code=400,
            detail="Provide either a file or text.",
        )

    assignment_content = student_text or ""
    metadata: dict = {}

    # --- File handling / text extraction ---
    if file:
        file_content = await file.read()
        ctype = file.content_type or ""

        doc_text = ""
        dom_size = None
        dom_name = None

        if ctype == "application/pdf":
            doc_text, dom_size, dom_name = extract_text_from_pdf(file_content)
        elif ctype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            doc_text, dom_size, dom_name = extract_text_from_docx(io.BytesIO(file_content))
        elif ctype in ("image/png", "image/jpeg", "image/webp"):
            doc_text, dom_size, dom_name = extract_text_from_image(file_content)
        elif ctype == "text/plain":
            doc_text = file_content.decode("utf-8", errors="ignore")

        assignment_content = (assignment_content or "") + "\n\n" + (doc_text or "")

        if dom_size:
            metadata["font_size"] = dom_size
        if dom_name:
            metadata["font_name"] = dom_name

    if not assignment_content.strip():
        raise HTTPException(
            status_code=400,
            detail="Could not extract text.",
        )

    metadata["word_count"] = len(assignment_content.split())

    # --- Call OpenAI model ---
    try:
        if not openai.api_key:
            raise Exception("OPENAI_API_KEY not set on server.")

        response = client.chat.completions.create(
            model="gpt-4o",  # Use gpt-4o or gpt-4-turbo for best results
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": get_system_prompt()},
                {
                    "role": "user",
                    "content": create_user_prompt(
                        requirements=requirements,
                        metadata=metadata,
                        student_text=assignment_content,
                    ),
                },
            ],
            temperature=0.2,
        )

        # Parse the JSON returned by the model into our Pydantic model
        ai_data = AssignmentScoreResponse.model_validate_json(
            response.choices[0].message.content
        )

        # Build final response, adding full_text & restoring metadata
        final_response = ServerResponse(
            **ai_data.model_dump(),
            full_text=assignment_content,
        )
        final_response.dominant_font_name = metadata.get("font_name")
        final_response.dominant_font_size = metadata.get("font_size")

        return final_response

    except Exception as e:
        print(f"AI Error: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e),
        )