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
| Chat       | Ollama `qwen3.5:9b` (configurable) |
| Vector DB  | Chroma                        |
| UI         | Streamlit                     |

Tested with: `chromadb>=1.5.9`, `streamlit>=1.59.2`, `ollama>=0.6.2`,
`ollama/ollama:rocm`.

## GPU on AMD APUs (important)

This lab is verified on an **AMD Ryzen 7 8700G with Radeon 780M (gfx1103)**.
Ollama's default image runs on CPU only on this hardware. The compose file
uses `ollama/ollama:rocm` plus these settings so inference runs on the APU:

- `--device /dev/kfd --device /dev/dri` and `--group-add 105 --group-add 44`
  (render + video groups) so the container can reach the GPU.
- `HSA_OVERRIDE_GFX_VERSION=11.0.0` — tricks ROCm into treating gfx1103 as
  the supported gfx1100.
- `OLLAMA_IGPU_ENABLE=1` — Ollama disables integrated GPUs by default.

Confirm it works with `docker exec rag-ollama ollama ps` — the chat model
should report `100% GPU`, not `100% CPU`.

**Model size:** an APU shares system RAM as VRAM. Use 9B-class (or smaller)
chat models for full GPU offload; very large models can exhaust memory. The
default `qwen3.5:9b` (6.6 GB) offloads 34/34 layers to the APU.

## Run

```bash
docker compose up -d --build
```

Wait for Ollama to be healthy, then open <http://localhost:8501>.

## Use

1. Click **Pull models** in the sidebar (downloads `qwen3-embedding` and
   the default chat model, `qwen3.5:9b`, into Ollama). Pick other chat
   models from the **Chat model** dropdown.
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

## Models & lessons learned

Verified locally on Docker 26 + Ollama (Linux). Models are pulled into the
`ollama` container, not the host.

| Role      | Default model            | Size   | Notes |
|-----------|--------------------------|--------|-------|
| Embedding | `qwen3-embedding`        | 4.7 GB | SOTA Ollama embedding model (MTEB). |
| Chat      | `qwen3.5:9b`             | 6.6 GB | Default chat model; offloads 34/34 layers to the APU GPU. |
| Alt chat  | `gemma4:latest`          | 9.6 GB | Smaller Gemma 4 option; works through the RAG pipeline. |
| Alt chat  | `gemma4:26b-a4b-it-qat`  | 15 GB | Larger; fits the APU with partial offload. |
| Small     | `qwen3:0.6b`             | 522 MB | Fast, weak — good for the automated benchmark. |

Lessons:

- **Ollama tag naming is strict.** The model you want is
  `gemma4:26b-a4b-it-qat` (under the `gemma4` namespace), not
  `gemma-4-26b-a4b-qat` — the latter returns "manifest file does not exist"
  even though a search page exists for it. When a pull fails with that error,
  the blob is not published yet; try a sibling tag or the `:latest` base.
- **Large models take time.** Pull them in the background
  (`docker exec <ollama> ollama pull <model> &`) and poll `ollama list`.
- **Inside the compose network, use service hostnames.** Ollama is
  `http://ollama:11434` and Chroma is `http://chroma:8000` from the app
  container — `localhost` will refuse the connection.
- **Hybrid retrieval + citations** materially improved answer trust: every
  claim carries a `[n]` marker resolvable to a filename + chunk + distance.
- **APU model size matters.** On the Ryzen 7 8700G / 780M, the 23 GB
  `qwen3.6` **crashes the machine** via Ollama's iGPU memory handling. Use
  <=~9B for the APU; the 0.6B model is ideal for the automated benchmark.

## Metrics reference (benchmark)

`tests/benchmark.py` runs each chat model over the 8 Q&A in `tests/corpus.py`
and reports, averaged per model:

| Metric | Range | Definition | Why it matters |
|--------|-------|-------------|----------------|
| `retrieval_recall@8` | 0–1 | fraction of questions whose expected source chunk is in the top-8 retrieved hits | did the retriever find the right evidence? |
| `citation_accuracy` | 0–1 | valid `[n]` markers / total `[n]` markers (each must map to a real retrieved chunk) | are claims traceable, not hallucinated citations? |
| `faithfulness` | 0–1 | \|answer content-words ∩ retrieved-context words\| / \|answer content-words\| (stopwords removed) | is the answer grounded in retrieved context? offline proxy for groundedness |
| `retrieve_latency_ms` | ms | wall time of `retrieve()` | retrieval cost |
| `generate_latency_ms` | ms | wall time of `answer()` minus retrieve | generation cost (includes iGPU warmup on first call) |
| `answer_length` | int | characters in the final answer | verbosity comparison |
| `tokens_per_sec` | tok/s | `eval_count / eval_duration_s` from Ollama | model + hardware throughput |

All metrics are **offline** — no external judge model, no internet. Raw
per-item values are written to `tests/results/benchmark_<ts>.json` for
audit. Run it with the stack up:

```bash
docker exec -e LOCALRAG_COMPOSE=1 rag-app python3 tests/benchmark.py --models qwen3.5:9b,gemma4:latest
```

### Example result (qwen3:0.6b vs gemma4:latest, 100% GPU on the APU)

| model | retrieval_recall@8 | citation_accuracy | faithfulness | retrieve_ms | generate_ms | answer_len |
|-------|---------------------|-------------------|-------------|------------|-------------|------------|
| qwen3:0.6b | 0.875 | 1.0 | 0.761 | 238 | 4,256 | 195 |
| gemma4:latest | 0.875 | 1.0 | 0.833 | 1,717 | 27,840 | 174 |

Both retrieve the same gold chunks and cite perfectly. `gemma4:latest` is
more faithful (0.833 vs 0.761) but far slower to generate on the Radeon 780M
iGPU (27.8 s vs 4.3 s per answer). Use a small model for fast sweeps; spot-
check larger models manually (they can take minutes per reply on the APU).

