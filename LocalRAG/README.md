# Local RAG Notebook

A fully offline Retrieval-Augmented Generation lab. Ingestion, embeddings,
and chat all run locally: **Ollama** serves the models, **Chroma** stores
the vectors, and a **Streamlit** notebook is the interface. Nothing is sent
to the cloud.

## What you build

- A document store that lives on your own disk (Chroma).
- An ingestion step that chunks your files and embeds them with a local model.
- A chat UI that retrieves the most relevant chunks and asks a local LLM to
  answer strictly from your context.

## Stack

| Layer      | Tool                          |
|------------|-------------------------------|
| Embeddings | Ollama `qwen3-embedding`     |
| Chat       | Ollama `qwen3.5` (configurable) |
| Vector DB  | Chroma                        |
| UI         | Streamlit                     |

Tested with: `chromadb>=1.5.9`, `streamlit>=1.59.2`, `ollama>=0.6.2`.

## Run

```bash
docker compose up -d --build
```

Wait for Ollama to be healthy, then open <http://localhost:8501>.

## Use

1. Click **Pull models** in the sidebar (downloads `qwen3-embedding` and
   `qwen3.5` into Ollama).
2. Drop your own files into `./docs` (`.txt`, `.md`, `.org`, `.pdf`, `.json`).
3. Click **Ingest docs**.
4. Ask questions in the chat box. Answers are grounded only in your documents.

## Configure

Set environment variables in `docker-compose.yml` to change models:

- `EMBED_MODEL` — any Ollama embedding model (e.g. `nomic-embed-text-v2-moe`, `mxbai-embed-large`).
- `CHAT_MODEL` — any Ollama chat model (e.g. `llama4`, `gemma4`, `mistral-small3.2`).

## How it works

1. `ingest()` reads `./docs`, splits each file into ~800-char chunks, and
   upserts them into Chroma with Ollama embeddings.
2. `answer()` queries Chroma for the 4 nearest chunks, packs them as context,
   and calls the local LLM with a strict "use only the context" prompt.
3. The LLM never sees anything outside the retrieved context, so answers stay
   grounded in your data.

## Offline note

All models are pulled into the `ollama` container. Once pulled, the lab works
with no internet connection.
