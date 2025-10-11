# rag_pipeline.py
from pymongo import MongoClient
from sentence_transformers import SentenceTransformer, util
import openai
import torch

# ---------------- MongoDB setup ----------------
client = MongoClient("mongodb+srv://Manny0715:Manmeet12345@cluster0.1pf6oxg.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0") 
db = client.smartassist
kb_collection = db.knowledge_base

# ---------------- Embeddings ----------------
embed_model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')

# ---------------- OpenAI setup ----------------
openai.api_key = " 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"  # <<< replace with your key

# ---------------- RAG retrieval ----------------
def retrieve_relevant_articles(question, top_k=3):
    articles = list(kb_collection.find({}))
    if not articles:
        return []

    texts = [a['content'] for a in articles]
    embeddings = embed_model.encode(texts, convert_to_tensor=True)
    q_emb = embed_model.encode(question, convert_to_tensor=True)

    scores = util.cos_sim(q_emb, embeddings)[0]
    top_results = torch.topk(scores, k=min(top_k, len(articles)))

    relevant_articles = [
        articles[i] for i in top_results.indices.tolist() if scores[i] > 0.5
    ]
    return relevant_articles

# ---------------- Generate answer ----------------
def get_answer(question, top_k=3):
    context_articles = retrieve_relevant_articles(question, top_k=top_k)
    
    if not context_articles:
        return "I’m not sure about that. You can try our live chat for help.", True

    context_text = "\n\n".join([f"{a['title']}: {a['content']}" for a in context_articles])

    # Use chat-completion format
    messages = [
        {
            "role": "system",
            "content": (
                "You are SmartAssist, a helpful AI assistant for Texas A&M University–Corpus Christi. "
                "Answer the student's question clearly and concisely using only the information from the context below. "
                "If the answer is not found in the context, politely say you are not sure."
            )
        },
        {
            "role": "user",
            "content": f"Context:\n{context_text}\n\nQuestion: {question}\nAnswer:"
        }
    ]

    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=messages,
        temperature=0.7,
        max_tokens=300
    )

    answer_text = response.choices[0].message['content'].strip()
    return answer_text, False
