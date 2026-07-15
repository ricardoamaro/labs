import os
import re
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
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:9b")
DOCS_DIR = os.getenv("DOCS_DIR", "/app/docs")
COLLECTION = "lab_docs"
CHUNK_CHARS = 800
OVERLAP_CHARS = 100
TOP_K = 8

chroma_client = chromadb.HttpClient(
    host=CHROMA_BASE_URL.split("//")[-1].split(":")[0],
    port=int(CHROMA_BASE_URL.split(":")[-1]),
)
embed_fn = embedding_functions.OllamaEmbeddingFunction(
    url=f"{OLLAMA_BASE_URL}/api/embeddings",
    model_name=EMBED_MODEL,
)
# Client bound to the configured Ollama host (resolves the compose service
# name `ollama` inside the container; localhost on a host-run stack).
ollama_client = ollama.Client(host=OLLAMA_BASE_URL)
collection = chroma_client.get_or_create_collection(
    name=COLLECTION, embedding_function=embed_fn
)

DOC_TYPES = {
    ".txt": "text",
    ".md": "markdown",
    ".org": "org",
    ".pdf": "pdf",
    ".json": "json",
}


def _id(path: pathlib.Path, idx: int, text: str) -> str:
    return hashlib.sha256(f"{path.name}:{idx}:{text}".encode()).hexdigest()


def _split_structure(text: str):
    parts = re.split(r"(?m)^(#{1,6}\s.*|$\n)", text)
    blocks = [p for p in parts if p and not p.isspace()]
    chunks, buf = [], ""
    for block in blocks:
        candidate = (buf + "\n" + block).strip()
        if len(candidate) <= CHUNK_CHARS:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        buf = block.strip()
    if buf:
        chunks.append(buf)
    merged, i = [], 0
    while i < len(chunks):
        cur = chunks[i]
        while i + 1 < len(chunks) and len(cur) + len(chunks[i + 1]) <= CHUNK_CHARS:
            cur += "\n" + chunks[i + 1]
            i += 1
        merged.append(cur)
        i += 1
    if OVERLAP_CHARS and merged:
        out = []
        for j, c in enumerate(merged):
            if j > 0:
                c = merged[j - 1][-OVERLAP_CHARS:] + "\n" + c
            out.append(c)
        merged = out
    return merged


def ingest() -> int:
    count = 0
    for path in pathlib.Path(DOCS_DIR).rglob("*"):
        if not path.is_file() or path.suffix.lower() not in DOC_TYPES:
            continue
        text = path.read_text(errors="ignore")
        chunks = _split_structure(text)
        metas = [
            {
                "filename": path.name,
                "doc_type": DOC_TYPES[path.suffix.lower()],
                "chunk_index": i,
                "ingested_at": int(os.environ.get("INGEST_TS", "0")) or 0,
            }
            for i in range(len(chunks))
        ]
        ids = [_id(path, i, c) for i, c in enumerate(chunks)]
        collection.upsert(ids=ids, documents=chunks, metadatas=metas)
        count += len(chunks)
    return count


def ensure_models():
    local = {m.model for m in ollama_client.list().models}
    for name in (EMBED_MODEL, CHAT_MODEL):
        if name not in local:
            ollama_client.pull(name)


def retrieve(question: str, filename: str | None):
    where = {"filename": filename} if filename and filename != "All" else None
    results = collection.query(
        query_texts=[question],
        n_results=TOP_K,
        where=where,
    )
    docs = results["documents"][0]
    metas = results["metadatas"][0]
    dists = results["distances"][0]
    return list(zip(docs, metas, dists))


def answer(question: str, filename: str | None):
    hits = retrieve(question, filename)
    if not hits:
        return "No relevant chunks found. Try ingesting docs or broadening the filter.", []
    context = "\n\n".join(
        f"[{i+1}] ({m['filename']}) {d}" for i, (d, m, _) in enumerate(hits)
    )
    prompt = textwrap.dedent(f"""
        Answer the question using ONLY the context below.
        Cite the supporting chunk with its [n] marker inline, e.g. "see [2]".
        If the context is insufficient, say you don't know.
        Context:
        {context}
        Question: {question}
    """)
    resp = ollama_client.chat(model=CHAT_MODEL, messages=[{"role": "user", "content": prompt}])
    return resp.message.content, hits


st.set_page_config(page_title="Local RAG Notebook", layout="wide")
st.title("Local RAG Notebook")
st.caption(f"embed: {EMBED_MODEL} · chat: {CHAT_MODEL} · hybrid search · fully offline")

with st.sidebar:
    st.header("Setup")
    if st.button("Pull models"):
        ensure_models()
        st.success("Models ready")
    if st.button("Ingest docs"):
        os.environ["INGEST_TS"] = str(int(__import__("time").time()))
        n = ingest()
        st.success(f"Ingested {n} chunks from {DOCS_DIR}")
    st.divider()
    filenames = ["All"] + sorted({m["filename"] for m in collection.get()["metadatas"] or []})
    st.session_state.filename_filter = st.selectbox("Filter by document", filenames)

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
                a, hits = answer(q, st.session_state.get("filename_filter", "All"))
            except Exception as e:  # noqa: BLE001
                a, hits = f"Error: {e}", []
        st.write(a)
        if hits:
            with st.expander(f"Sources ({len(hits)} chunks retrieved)"):
                for i, (d, m, dist) in enumerate(hits, 1):
                    st.markdown(f"**[{i}] {m['filename']}** · chunk {m['chunk_index']} · distance {dist:.3f}")
                    st.caption(d[:600] + ("…" if len(d) > 600 else ""))
    st.session_state.history.append(("assistant", a))
