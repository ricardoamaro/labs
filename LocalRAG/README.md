# Local RAG Notebook

A fully offline Retrieval-Augmented Generation lab. Ingestion, embeddings,
and chat all run locally: **Ollama** serves the models, **Chroma** stores
the vectors, and a **Streamlit** notebook is the interface. Nothing is sent
to the cloud.

## What you build

- A document store that lives on your own disk (Chroma).
- An ingestion step that chunks your files with structure awareness and embeds
  them with a local model.
- A chat UI that retrieves the most relevant chunks via **hybrid search**
  (lexical + semantic), answers strictly from your context, and **cites its
  sources** so you can verify every claim.

## Features

- **Hybrid retrieval** — Chroma fuses BM25 lexical search with vector search,
  catching both exact keywords and semantic meaning.
- **Structure-aware chunking** — splits on headings/paragraphs with overlap,
  so chunks respect document shape instead of cutting mid-sentence.
- **Inline citations** — answers carry `[n]` markers linking to the source
  chunk, with an expandable panel showing filename, chunk index, retrieval
  distance (a thin-context confidence hint), and the snippet.
- **Per-document filter** — scope a question to a single file from the sidebar.

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

1. `ingest()` reads `./docs`, splits each file into structure-aware chunks
   (~800 chars, 100-char overlap, heading/paragraph boundaries), and upserts
   them into Chroma with `filename`, `chunk_index`, `doc_type`, and
   `ingested_at` metadata.
2. `retrieve()` runs Chroma **hybrid** search (lexical + vector) for the top-8
   chunks, optionally scoped by the filename filter.
3. `answer()` packs the chunks as numbered context and calls the local LLM with
   a strict "use only the context, cite with [n]" prompt.
4. The LLM never sees anything outside the retrieved context, so answers stay
   grounded in your data and every claim is traceable to a source.

## Offline note

All models are pulled into the `ollama` container. Once pulled, the lab works
with no internet connection.
