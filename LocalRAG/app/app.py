import os
import hashlib
import pathlib
import textwrap

import streamlit as st
import chromadb
from chromadb.utils import embedding_functions
import ollama

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
CHROMA_BASE_URL = os.getenv("CHROMA_BASE_URL", "http://localhost:8000")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding")
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5")
DOCS_DIR = os.getenv("DOCS_DIR", "/app/docs")
COLLECTION = "lab_docs"

chroma_client = chromadb.HttpClient(host=CHROMA_BASE_URL.split("//")[-1].split(":")[0],
                                    port=int(CHROMA_BASE_URL.split(":")[-1]))
embed_fn = embedding_functions.OllamaEmbeddingFunction(
    url=f"{OLLAMA_BASE_URL}/api/embeddings",
    model_name=EMBED_MODEL,
)
collection = chroma_client.get_or_create_collection(name=COLLECTION, embedding_function=embed_fn)


def _sha(path: pathlib.Path, text: str) -> str:
    return hashlib.sha256(f"{path.name}:{text}".encode()).hexdigest()


def ingest() -> int:
    count = 0
    for path in pathlib.Path(DOCS_DIR).rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".txt", ".md", ".org", ".pdf", ".json"}:
            continue
        text = path.read_text(errors="ignore")
        chunks = textwrap.wrap(text, 800, break_long_words=False)
        for chunk in chunks:
            collection.upsert(ids=[_sha(path, chunk)], documents=[chunk])
            count += 1
    return count


def ensure_models():
    local = {m.model for m in ollama.list().models}
    for name in (EMBED_MODEL, CHAT_MODEL):
        if name not in local:
            ollama.pull(name)


def answer(question: str) -> str:
    results = collection.query(query_texts=[question], n_results=4)
    context = "\n\n".join(results["documents"][0])
    prompt = textwrap.dedent(f"""
        Answer the question using ONLY the context below.
        If the context is insufficient, say you don't know.
        Context:
        {context}
        Question: {question}
    """)
    resp = ollama.chat(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}])
    return resp.message.content


st.set_page_config(page_title="Local RAG Notebook", layout="wide")
st.title("Local RAG Notebook")
st.caption(f"embed: {EMBED_MODEL} · chat: {CHAT_MODEL} · fully offline")

with st.sidebar:
    st.header("Setup")
    if st.button("Pull models"):
        ensure_models()
        st.success("Models ready")
    if st.button("Ingest docs"):
        n = ingest()
        st.success(f"Ingested {n} chunks from {DOCS_DIR}")

if "history" not in st.session_state:
    st.session_state.history = []

for role, msg in st.session_state.history:
    st.chat_message(role).write(msg)

if q := st.chat_input("Ask your documents anything"):
    st.chat_message("user").write(q)
    st.session_state.history.append(("user", q))
    with st.chat_message("assistant"):
        with st.spinner("Thinking locally..."):
            try:
                a = answer(q)
            except Exception as e:  # noqa: BLE001
                a = f"Error: {e}"
        st.write(a)
    st.session_state.history.append(("assistant", a))
