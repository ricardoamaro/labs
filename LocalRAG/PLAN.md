# LocalRAG — Plan, Status & Next Steps

This file is the single source of truth for the LocalRAG lab: what it is,
what is built and verified, and what remains. Updated as we go.

## 1. Goal

A fully offline Retrieval-Augmented Generation lab (Ollama + Chroma +
Streamlit) with:
- Hybrid retrieval (BM25 + vector), structure-aware chunking, inline
  citations with a source panel, per-document filter.
- A model-comparison **benchmark harness** that scores chat models on
  retrieval quality + answer groundedness, fully offline.
- A **pytest** suite for the pure logic.
- Correct **GPU acceleration** on the AMD Ryzen 7 8700G / Radeon 780M APU.

## 2. What is DONE and VERIFIED

### Lab (app.py, docker-compose.yml, README.md)
- Offline RAG pipeline: ingest -> hybrid retrieve -> grounded chat.
- Structure-aware chunking (heading/paragraph splits, overlap, metadata).
- Inline `[n]` citations + expandable source panel with retrieval distance.
- Per-document filename filter in the UI sidebar.
- `ollama.Client(host=OLLAMA_BASE_URL)` so chat/ensure_models reach the
  compose `ollama` service (was defaulting to localhost — fixed).
- Streamlit UI live at http://localhost:8501 (HTTP 200 confirmed).

### GPU on AMD APU (LOG.md documents the full investigation)
- `ollama/ollama:rocm` + `--device /dev/kfd --device /dev/dri` +
  `--group-add 105 --group-add 44` (render + video) +
  `HSA_OVERRIDE_GFX_VERSION=11.0.0` + `OLLAMA_IGPU_ENABLE=1`.
- Verified: `qwen3.5:9b` offloads **34/34 layers to GPU** (100% GPU in
  `ollama ps`). `qwen3:0.6b` also 100% GPU.
- Embedding model `qwen3-embedding` runs on GPU too.

### Tests / benchmark (tests/)
- `tests/test_chunking.py` — 5 pure unit tests, **PASS** (no models needed).
- `tests/benchmark.py` — runs each chat model over 8 Q&A, reports
  retrieval_recall@8, citation_accuracy, faithfulness, latencies,
  tokens/s; writes JSON to tests/results/. **VERIFIED** on the APU.
- `tests/conftest.py` — streamlit stub (so app.py imports), compose
  hostname wiring via LOCALRAG_COMPOSE=1.
- `tests/corpus.py` — 8 Q&A over docs/sample.md.
- `requirements-dev.txt`, `.gitignore` (ignores tests/results/).

### Benchmark result (qwen3:0.6b, 522 MB, 100% GPU, warmed run)
| metric              | value |
|---------------------|-------|
| retrieval_recall@8  | 0.875 |
| citation_accuracy   | 1.0   |
| faithfulness        | 0.651 |
| retrieve_latency_ms | 240.5 |
| generate_latency_ms | 5088.8 |
| answer_length       | 168   |

First run (cold) had generate_latency ~17s; warmed run ~5s. The 0.6B model
is weak but proves the pipeline + metrics work end-to-end on GPU. (Multi-model
table with gemma4:latest is in README; the 9B model was NOT benchmarked
end-to-end — see constraints.)

## 3. Committed since last plan write

- `tests/benchmark.py` — per-question threaded timeout (`_timed_answer`, 300s)
  so a slow APU model can't hang the whole run.
- `docker-compose.yml` — mounts `./tests:/app/tests` into rag-app (no
  `docker cp`); duplicate `volumes` key removed.
- `LOG.md` — benchmark result + APU runtime findings.
- `.gitignore` — ignore `.pytest_cache/`, `__pycache__/`.
- `PLAN.md` — this file.
- All pushed to GitLab + GitHub (commit `ff876e2`).

## 4. Known issues / constraints

- **APU is slow for big models.** 9B model offloads to GPU but first
  generation took ~9 min (iGPU warmup). 23 GB qwen3.6 **crashed the whole
  machine** via Ollama's iGPU memory handling. Use <=~9B; prefer small
  models for the automated benchmark.
- **`rag-app` is recreated by `docker compose up`** — now mitigated by
  mounting ./tests, but the app image itself has no tests; they come from
  the mount.
- The benchmark imports app.py via importlib + streamlit stub; works but is
  fragile if app.py grows more streamlit surface at import time.
- No CI yet; `pytest -m "not benchmark"` runs the fast tests only.

## 5. NEXT STEPS (in order)

1. ~~**Commit + push** the in-progress changes (benchmark timeout, tests
   mount, LOG update) to GitLab + GitHub.~~ DONE (`ff876e2`).
2. **Add a chat-model selector to the UI** (sidebar dropdown of available
   Ollama models) so users can switch qwen3.5:9b / gemma4 / etc. live
   without env vars. Default to qwen3.5:9b. **DONE** — sidebar dropdown
   lists pulled chat models, `answer()` reads `st.session_state.chat_model`.
3. **Document metrics in README** — "Metrics reference" section added with
   metric definitions + a multi-model example result; fixed stale model table
   (was pointing at the crashing qwen3.6). **DONE.**
 4. **Benchmark more models** on the APU — compare qwen3:0.6b vs gemma4:latest
    (both verified, 2-model table in README + LOG). **DONE for 2 models.**
    The `qwen3.5:9b` (9B) model was NOT run end-to-end in the benchmark: it
    offloads 34/34 layers to GPU but the first generation takes ~9 min on the
    Radeon 780M iGPU, so it is reserved for manual spot-checks. Results:
    gemma4:latest faithfulness 0.833 vs qwen3:0.6b 0.651; gemma4 much slower
    to generate on the iGPU (27.8s vs 4.3s). Use small models for sweeps.
5. **Add a CI workflow** (GitHub Actions) running `pytest -m "not
   benchmark"` on PRs; benchmark as an optional manual job (needs GPU).
6. **Optional LLM-as-judge**: add an opt-in faithfulness/relevance judge
   using a local model (toggle, since offline). Currently faithfulness is
   overlap-based.
7. **Next creative lab**: build another lab from the shortlist (model
   arena, voice chatbot, image-gen studio) reusing this stack pattern.

## 6. How to run (recap)

```
cd LocalRAG
docker compose up -d --build        # ollama(rocm, GPU) + chroma + app
open http://localhost:8501           # Pull models -> Ingest docs -> ask

# tests (no models)
python -m pytest tests/test_chunking.py -v

# benchmark (needs stack up; runs inside app container)
docker exec -e LOCALRAG_COMPOSE=1 rag-app python3 tests/benchmark.py --models qwen3:0.6b
# results -> tests/results/benchmark_<ts>.json
```

## 7. URLs
- Streamlit UI: http://localhost:8501
- Ollama API:   http://localhost:11434
- Chroma:       http://localhost:8000
