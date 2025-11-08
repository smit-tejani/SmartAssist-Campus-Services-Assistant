# rag_pipeline.py
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer, util
from huggingface_hub import InferenceClient
import torch
import os
from threading import Lock
from dotenv import load_dotenv

load_dotenv()
# ---------------- MongoDB setup ----------------
# (Consider moving secrets out of source later, but left inline as in your original file.)
client = MongoClient(os.getenv("MONGODB_URI"))
db = client.smartassist
kb_collection = db.knowledge_base
courses_collection = db.courses


# ---------------- Embeddings (Retrieval) ----------------
embed_model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")

# ---------------- Hugging Face Inference (Generation) ----------------
# Inline token (per your request). Replace with your actual token string.
HF_TOKEN = os.getenv("HF_TOKEN")

# Choose a strong instruct model. Good options:
#   - "meta-llama/Meta-Llama-3-8B-Instruct"
#   - "mistralai/Mixtral-8x7B-Instruct-v0.1"
#   - "HuggingFaceH4/zephyr-7b-beta"
HF_MODEL = "meta-llama/Meta-Llama-3-8B-Instruct"

hf_client = InferenceClient(api_key=HF_TOKEN)

# ---------------- RAG retrieval ----------------
_learning_lock = Lock()
_learning_docs = []
_learning_texts = []
_learning_embeddings = None
_learning_revision = None


def _ensure_learning_cache():
    global _learning_docs, _learning_texts, _learning_embeddings, _learning_revision

    with _learning_lock:
        current_revision = courses_collection.count_documents({})
        if (
            _learning_embeddings is not None
            and _learning_revision is not None
            and _learning_revision == current_revision
        ):
            return

        docs = list(courses_collection.find({}))
        texts = []
        for doc in docs:
            title = (doc.get("title") or "Unnamed course").strip()
            term = (doc.get("term") or "").strip()
            details = (doc.get("details") or "").strip()
            hours = doc.get("hours")
            crn = doc.get("crn")
            schedule = (doc.get("schedule_type") or "").strip()
            grade_mode = (doc.get("grade_mode") or "").strip()
            level = (doc.get("level") or "").strip()
            part = (doc.get("part_of_term") or "").strip()

            summary_bits = [title]
            if term:
                summary_bits.append(f"term: {term}")
            if details:
                summary_bits.append(f"details: {details}")
            if hours is not None:
                summary_bits.append(f"credit hours: {hours}")
            if crn is not None:
                summary_bits.append(f"CRN: {crn}")
            if schedule:
                summary_bits.append(f"schedule type: {schedule}")
            if grade_mode:
                summary_bits.append(f"grade mode: {grade_mode}")
            if level:
                summary_bits.append(f"level: {level}")
            if part:
                summary_bits.append(f"part of term: {part}")

            texts.append("; ".join(summary_bits))

        if texts:
            embeddings = embed_model.encode(
                texts, convert_to_tensor=True, normalize_embeddings=True
            )
        else:
            embeddings = None

        _learning_docs = docs
        _learning_texts = texts
        _learning_embeddings = embeddings
        _learning_revision = current_revision


def _retrieve_learning_articles(question: str, top_k: int = 3, score_threshold: float = 0.2):
    _ensure_learning_cache()

    if not _learning_docs or _learning_embeddings is None:
        return []

    q_emb = embed_model.encode(question, convert_to_tensor=True, normalize_embeddings=True)
    scores = util.cos_sim(q_emb, _learning_embeddings)[0]
    top_results = torch.topk(scores, k=min(top_k, len(_learning_docs)))
    idxs = top_results.indices.tolist()

    relevant = []
    for i in idxs:
        if float(scores[i]) >= score_threshold:
            course_doc = dict(_learning_docs[i])
            course_doc["title"] = f"{course_doc.get('title', 'Course')} ({course_doc.get('term', 'Term TBD')})"
            course_doc["content"] = _learning_texts[i]
            relevant.append(course_doc)
    return relevant


def retrieve_relevant_articles(question, top_k=3, score_threshold=0.5, mode="uni"):
    if mode == "learning":
        return _retrieve_learning_articles(question, top_k=top_k, score_threshold=0.25)

    articles = list(kb_collection.find({}))
    if not articles:
        return []

    texts = [a.get("content", "") for a in articles]
    if not texts:
        return []

    embeddings = embed_model.encode(texts, convert_to_tensor=True, normalize_embeddings=True)
    q_emb = embed_model.encode(question, convert_to_tensor=True, normalize_embeddings=True)

    scores = util.cos_sim(q_emb, embeddings)[0]  # cosine similarities
    top_results = torch.topk(scores, k=min(top_k, len(articles)))
    idxs = top_results.indices.tolist()

    relevant = []
    for i in idxs:
        if float(scores[i]) >= score_threshold:
            relevant.append(articles[i])
    return relevant

# ---------------- Helpers ----------------
def format_sources_md(articles):
    """Build a markdown sources list with clickable links (if present)."""
    if not articles:
        return ""
    lines = []
    seen = set()
    for a in articles:
        title = (a.get("title") or "Untitled").strip()
        url = (a.get("url") or a.get("source") or "").strip()  # support either field name
        key = (title, url)
        if key in seen:
            continue
        seen.add(key)
        if url:
            lines.append(f"- [{title}]({url})")
        else:
            lines.append(f"- {title}")
    return "\n".join(lines)

def build_prompt(context_text, question, mode="uni"):
    """Instruction for well-formatted, grounded answers in Markdown."""
    if mode == "learning":
        role = (
            "You are SmartAssist My Learning, a mentor helping TAMU-CC students understand their courses, "
            "registration details, schedules, and academic expectations."
        )
        constraints = (
            "Focus on course availability, credit hours, CRNs, schedules, and terms. "
            "If the information is not in the context, say you are not sure and suggest checking with an advisor."
        )
    else:
        role = "You are SmartAssist, a helpful AI assistant for Texas A&M University–Corpus Christi."
        constraints = (
            "Use ONLY the information in the provided Context. Write a concise, well-formatted Markdown answer. "
            "If the answer is not covered in the Context, say you are not sure. Do not invent facts."
        )

    return f"""
{role}

Follow the rules strictly:
- {constraints}

# Context
{context_text}

# Question
{question}

# Answer
""".strip()

# ---------------- Generate answer (streaming) ----------------
def get_answer_stream(question, top_k=3, mode="uni"):
    """Generator function that yields chunks of the answer for streaming."""
    context_articles = retrieve_relevant_articles(question, top_k=top_k, mode=mode)

    if not context_articles:
        if mode == "learning":
            yield (
                "I'm not sure about that course. Try asking about a specific class, term, or CRN, "
                "or switch back to University Mode for campus services."
            )
        else:
            yield "I'm not sure about that. You can try our live chat for help."
        return

    # Keep context compact but informative: "Title: content"
    context_chunks = [
        f"{(a.get('title') or 'Untitled').strip()}: {(a.get('content') or '').strip()}"
        for a in context_articles
    ]
    context_text = "\n\n".join(context_chunks)

    prompt = build_prompt(context_text, question, mode=mode)

    try:
        # Stream the completion
        stream = hf_client.chat_completion(
            model=HF_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are SmartAssist for Texas A&M University–Corpus Christi. "
                        "Answer ONLY from the provided Context. Use concise, well-formatted Markdown. "
                        "If it's not in the Context, say you're not sure."
                        if mode != "learning"
                        else "You are SmartAssist My Learning for Texas A&M University–Corpus Christi. "
                        "Answer ONLY from the provided Context, focusing on course logistics and academic details. "
                        "If it's not in the Context, say you're not sure and recommend contacting an advisor."
                    ),
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=450,
            temperature=0.2,
            top_p=0.9,
            stream=True
        )

        # Yield chunks as they arrive
        for chunk in stream:
            # Handle different response formats
            if hasattr(chunk, 'choices') and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'content') and delta.content:
                    yield delta.content
            elif isinstance(chunk, dict) and 'choices' in chunk and len(chunk['choices']) > 0:
                delta = chunk['choices'][0].get('delta', {})
                if 'content' in delta and delta['content']:
                    yield delta['content']

        # Add sources at the end
        sources_md = format_sources_md(context_articles)
        if sources_md:
            yield "\n\n---\n**Sources**\n" + sources_md
            
    except Exception as e:
        print(f"Error in streaming: {e}")
        # Fallback to non-streaming
        yield "I apologize, but I'm having trouble streaming the response. Let me try again.\n\n"
        # Use non-streaming as fallback
        full_answer, _ = get_answer(question, top_k, mode=mode)
        yield full_answer

# ---------------- Generate answer (non-streaming, for backwards compatibility) ----------------
def get_answer(question, top_k=3, mode="uni"):
    context_articles = retrieve_relevant_articles(question, top_k=top_k, mode=mode)

    if not context_articles:
        if mode == "learning":
            return (
                "I’m not sure about that course. Try asking with the course name, CRN, or term, "
                "or switch to University Mode for campus-wide questions.",
                True,
            )
        return "I’m not sure about that. You can try our live chat for help.", True

    # Keep context compact but informative: "Title: content"
    context_chunks = [
        f"{(a.get('title') or 'Untitled').strip()}: {(a.get('content') or '').strip()}"
        for a in context_articles
    ]
    context_text = "\n\n".join(context_chunks)

    prompt = build_prompt(context_text, question, mode=mode)

    completion = hf_client.chat_completion(
        model=HF_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are SmartAssist for Texas A&M University–Corpus Christi. "
                    "Answer ONLY from the provided Context. Use concise, well-formatted Markdown. "
                    "If it’s not in the Context, say you’re not sure."
                    if mode != "learning"
                    else "You are SmartAssist My Learning for Texas A&M University–Corpus Christi. "
                    "Answer ONLY from the provided Context, focusing on course logistics and academic details. "
                    "If it’s not in the Context, say you’re not sure and recommend contacting an advisor."
                ),
            },
            {"role": "user", "content": prompt}
        ],
        max_tokens=450,
        temperature=0.2,
        top_p=0.9,
    )

    answer_text = completion.choices[0].message["content"].strip()

    sources_md = format_sources_md(context_articles)
    if sources_md:
        answer_text += "\n\n---\n**Sources**\n" + sources_md

    return answer_text, False