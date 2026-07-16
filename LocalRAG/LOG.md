# LocalRAG — GPU / Ollama on AMD APU investigation log

This log records the live debugging of getting Ollama to use the AMD Radeon
780M (Ryzen 7 8700G) APU instead of CPU, for the LocalRAG lab.

## Hardware

- CPU/APU: AMD Ryzen 7 8700G w/ Radeon 780M Graphics
- GPU gfx target: **gfx1103** (RDNA3 mobile APU, "Phoenix")
- Device nodes: `/dev/kfd`, `/dev/dri/card0`, `/dev/dri/renderD128`
- Host groups: `render` GID=105, `video` GID=44
- Host already has working **Mesa RADV** Vulkan (LM Studio uses it).
  `vulkaninfo --summary` -> `deviceName = AMD Radeon Graphics (RADV PHOENIX)`.

## Key facts discovered

1. Host `/usr/bin/ollama` (0.32.0) runs CPU-only. The bundled ROCm in the
   Ollama image does **not** support gfx1103.
2. `ollama ps` shows `PROCESSOR = 100% CPU` when the GPU is not engaged.
3. The APU is an **iGPU**; Ollama disables iGPU by default. Must set
   `OLLAMA_IGPU_ENABLE=1`.
4. gfx1103 is unsupported by ROCm's rocBLAS. Trick it with
   `HSA_OVERRIDE_GFX_VERSION=11.0.0` (x.y.z format -> maps gfx1103 to gfx1100,
   which IS in the supported list). `gfx1100` (no dots) does NOT work.
5. Container must be able to open `/dev/kfd` + `/dev/dri`: pass the devices
   AND `--group-add 105 --group-add 44` (render + video).
6. **APU VRAM is shared system RAM and small.** Loading the 23 GB `qwen3.6`
   fully into the iGPU memory CRASHED THE WHOLE MACHINE (OOM/hang). Only
   partial offload is safe; use small models (<=~3B) for full GPU, or accept
   CPU/GPU split for large ones. Confirmed working reference:
   https://github.com/dazraf/ollama-rocm (same gfx1103 APU).

## Working container (final, for this APU)

```
docker run -d --name rag-ollama \
  --device /dev/kfd --device /dev/dri \
  --group-add 105 --group-add 44 \
  -v ollama-models:/root/.ollama \
  -e HSA_OVERRIDE_GFX_VERSION=11.0.0 \
  -e OLLAMA_IGPU_ENABLE=1 \
  -e OLLAMA_HOST=0.0.0.0:11434 \
  -p 11434:11434 \
  ollama/ollama:rocm
```

GPU detection line (success):
`inference compute id=0 library=ROCm compute=gfx1100 name=ROCm0
 description="AMD Radeon Graphics" type=iGPU`

## Dead ends (do not repeat)

- `HSA_OVERRIDE_GFX_VERSION=gfx1100` -> wrong format, device dropped.
- `ollama/ollama:latest` image -> no GPU detected (no ROCm/Vulkan for APU).
- Mounting whole host `/usr/lib/x86_64-linux-gnu` -> broke Ollama libs -> CPU.
- `LD_LIBRARY_PATH` to host libs -> stack smashing (exit 139).
- Ollama's own bundled Vulkan (`OLLAMA_VULKAN=1`) -> dead for this APU.
- Loading 23 GB qwen3.6 into iGPU -> machine crash. Use small models.
- `qwen3.6:3b` tag does not exist (manifest 404). Use a real small tag.

## Model sizes (for reference)

- qwen3.6:latest  -> 23 GB (crashed Ollama when fully loaded into iGPU)
- qwen3-embedding -> 4.7 GB (embedding; smaller, may partially offload)
- gemma4:26b-a4b-it-qat -> 15 GB
- gemma4:latest -> 9.6 GB

## Reality check (LM Studio, host)

LM Studio uses Mesa RADV on the same APU and handles MUCH more RAM for GPU:
- Favorite small model: `Qwen3.5-9B-Q4_K_M` = 6.5 GB -> works great.
  Path: ~/.lmstudio/models/lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf
- Larger: `gemma-4-26B-A4B-it-QAT-Q4_0` = 15.6 GB -> works.
  Path: ~/.lmstudio/models/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf
- User has pushed ~50 GB through LM Studio on this APU with no crash.

Conclusion: the earlier machine crash was Ollama's own iGPU memory handling
(building a 23 GB CPU+GPU split poorly), NOT a hard VRAM ceiling. LM Studio
proves the APU can take far more. So a 6.5 GB Qwen3.5-9B should be safe in
Ollama too, once offload is correct. Target model for the lab: Qwen3.5-9B.

## CONFIRMED WORKING (final config)

Model: `qwen3.5:9b` (6.6 GB) -> `ollama ps` shows `100% GPU`,
`offloaded 34/34 layers to GPU`. No crash. This matches the user's LM Studio
favorite (Qwen3.5-9B-Q4_K_M). Generation is fast and fully on the APU.

The exact `rag-ollama` container that works:

```
docker run -d --name rag-ollama \
  --device /dev/kfd --device /dev/dri \
  --group-add 105 --group-add 44 \
  -v ollama-models:/root/.ollama \
  -e HSA_OVERRIDE_GFX_VERSION=11.0.0 \
  -e OLLAMA_IGPU_ENABLE=1 \
  -e OLLAMA_HOST=0.0.0.0:11434 \
  -p 11434:11434 \
  ollama/ollama:rocm
```

Notes:
- `qwen3.6:latest` (23 GB) earlier CRASHED the machine when Ollama tried to
  manage its iGPU split. `qwen3.5:9b` (6.6 GB) is safe and fully offloads.
  Use 9B-class (or smaller) chat models for this APU.
- The shared `ollama-models` volume holds all pulled models; pull once.
- Embedding model `qwen3-embedding` (4.7 GB) also offloads partially.

## Benchmark harness status (tests/)

- `tests/test_chunking.py` — 5 pure unit tests, PASS (no models needed).
- `tests/benchmark.py` — runs each chat model over 8 Q&A, reports
  retrieval_recall@8, citation_accuracy, faithfulness, latencies, tokens/s.
- `tests/conftest.py` — stubs streamlit so app.py imports; wires compose
  hostnames when LOCALRAG_COMPOSE=1.
- The app's `answer()`/`ensure_models()` now use `ollama.Client(host=OLLAMA_BASE_URL)`
  so they resolve the `ollama` service name inside the container.

## Runtime findings on this APU (IMPORTANT)

- A 9B model (qwen3.5:9b, 6.6 GB) DOES offload 34/34 layers to GPU
  (`ollama ps` = 100% GPU), BUT the first generation is extremely slow on
  the iGPU — one reply took ~9 minutes (GPU kernel/context warmup on the
  Radeon 780M). Subsequent calls are faster but still impractical for a
  8-question benchmark in one shot.
- `qwen3:0.6b` (522 MB) runs on GPU and answers in ~12s including warmup.
  Practical for the benchmark harness on this hardware.
- Lesson: benchmark with a SMALL model (<=1B) on the APU; reserve 9B+
  for spot checks. The 23 GB qwen3.6 fully crashes the machine via Ollama.
- The Ollama Python client chat call can appear to "hang" — it is actually
  the slow first-generation; give it minutes, not seconds. HTTP 500s in the
  server log were client timeouts closing the connection, not server crashes.

## How to run the benchmark here

```
# stack must be up (docker compose up -d)
docker cp tests rag-app:/app/tests
docker exec -e LOCALRAG_COMPOSE=1 rag-app python3 tests/benchmark.py \
  --models qwen3:0.6b
# results land in tests/results/benchmark_<ts>.json
```

Note: `rag-app` is recreated by `docker compose up`, wiping copied tests;
re-copy after each `up`. Better: mount ./tests into the app service (TODO).

## Benchmark result (qwen3:0.6b, 522 MB, 100% GPU on APU)

Ran 8 Q&A from tests/corpus.py through retrieve()->answer() on the live
stack. Aggregate (after the model-param bug fix, see below):

| metric              | value |
|---------------------|-------|
| retrieval_recall@8  | 0.875 |
| citation_accuracy   | 1.0   |
| faithfulness        | 0.651 |
| retrieve_latency_ms | 240.1 |
| generate_latency_ms | 5510.9 (warmed; first run includes iGPU warmup) |
| answer_length       | 235   |

### Bug: model=None in benchmark (FIXED)
First multi-model run returned identical `[ERROR] 1 validation error for
ChatRequest model ... Input should be a valid string` for ALL models, with
faithfulness 0.043 and identical length 220. Root cause: `answer()` built the
model from `st.session_state.get("chat_model", CHAT_MODEL)` which resolved to
None in the benchmark context. Fixed by giving `answer(question, filename,
model=None)` an explicit model param; UI passes `st.session_state.chat_model`,
benchmark passes the loop model. After the fix, real model-specific answers
return and faithfulness is ~0.65.

Observations:
- Hybrid retrieval found the gold chunk in 7/8 questions (the miss was the
  "lexical" question — BM25+vector still missed one expected hint).
- citation_accuracy 1.0: every [n] mapped to a real retrieved chunk.
- generate_latency is high because the Radeon 780M iGPU is slow on generation;
  the 0.6B model is also weak. A 9B model is more accurate but ~9 min for the
  first reply (see Runtime findings).
- Do NOT run two benchmark processes at once — they contend for the single
  APU and both stall. One run at a time.

## Status
- GPU config, chunking tests (5 pass), benchmark harness, UI model selector,
  metrics docs, and the model-param fix are all done locally. Awaiting user
  commit/push.
- Multi-model comparison: 2 models verified (qwen3:0.6b vs gemma4:latest).
  9B model (qwen3.5:9b) is verified for GPU offload only, not benchmarked
  end-to-end (too slow on the iGPU).
