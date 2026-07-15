# LocalRAG — GPU / Ollama on AMD APU investigation log

This log records the live debugging of getting Ollama to use the AMD Radeon
780M (Ryzen 7 8700G) APU instead of CPU, for the LocalRAG lab. Written as a
human would while exploring, so future runs can skip the dead ends.

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

## Reality check from user (LM Studio, host)

LM Studio uses Mesa RADV on the same APU and handles MUCH more RAM for GPU:
- Favorite small model: `Qwen3.5-9B-Q4_K_M` = 6.5 GB -> works great.
  Path: /home/ricardo/.lmstudio/models/lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q4_K_M.gguf
- Larger: `gemma-4-26B-A4B-it-QAT-Q4_0` = 15.6 GB -> works.
  Path: /home/ricardo/.lmstudio/models/lmstudio-community/gemma-4-26B-A4B-it-QAT-GGUF/gemma-4-26B-A4B-it-QAT-Q4_0.gguf
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

## Next

- Update LocalRAG docker-compose.yml: `ollama` service uses
  `ollama/ollama:rocm` + devices + group_add + env vars; mount the shared
  `ollama-models` volume. Default CHAT_MODEL -> `qwen3.5:9b`.
- Document the APU/GPU requirement in README (ROCm image, gfx override,
  iGPU enable, group perms, model-size guidance).
- Resume the benchmark harness work (tests/) that was paused for this.

## Next

- Find a working small chat model tag, pull it, run, confirm `ollama ps`
  shows GPU (not 100% CPU) without crashing.
- Update LocalRAG docker-compose.yml to use `ollama/ollama:rocm` + the env
  vars + group_add, and document the APU constraint in README.
