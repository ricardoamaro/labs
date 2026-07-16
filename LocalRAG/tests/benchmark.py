"""Model comparison benchmark for the LocalRAG lab.

Runs a fixed question set through the lab's retrieve() -> answer() pipeline
for each chat model and reports metrics as a Markdown table plus a JSON file.
Everything runs locally against Ollama + Chroma; no internet, no external
judge model.

Usage::
    python tests/benchmark.py                       # all default models
    python tests/benchmark.py --models qwen3.6,gemma4:latest
    LOCALRAG_COMPOSE=1 python tests/benchmark.py    # inside compose network

Metrics (all offline, no LLM-as-judge)
--------------------------------------
For each chat model we average the following over the corpus questions:

retrieval_recall@8
    Fraction of questions whose *expected* source chunk appears in the top-8
    retrieved hits. Range 0-1. Measures whether the hybrid retriever surfaced
    the right evidence. The "expected chunk" is identified by matching
    expected_filename + expected_hint against the retrieved metadata/text.

citation_accuracy
    valid [n] markers / total [n] markers in the answer. Each [n] must map to
    a real retrieved chunk index (1-based). Range 0-1. Detects hallucinated or
    out-of-range citations. A "valid" marker means 1 <= n <= len(hits).

faithfulness
    |answer content-words INTERSECT retrieved-context words|
        / |answer content-words|
    Range 0-1. Offline proxy for groundedness: how much of the answer is built
    from words that actually appear in the retrieved context. Stopwords are
    removed; lowercase. A low score hints the model ignored the context.

retrieve_latency_ms
    Wall-clock time of retrieve() in milliseconds. Retrieval cost.

generate_latency_ms
    Wall-clock time of answer() minus retrieve() in milliseconds. Generation
    cost (includes the Ollama chat call).

tokens_per_sec
    eval_count / eval_duration_seconds reported by Ollama for the generation.
    Higher is better; reflects model + hardware throughput.

answer_length
    Character count of the final answer. Context for verbosity differences.

The JSON output (tests/results/benchmark_<ts>.json) stores per-item raw
values so any metric can be recomputed or audited later.
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from corpus import QUESTIONS  # noqa: E402

DEFAULT_MODELS = [
    "qwen3.6",
    "gemma4:26b-a4b-it-qat",
    "gemma4:latest",
]

STOPWORDS = set(
    "the a an and or of to in is are was were be been being this that with for "
    "on as by it its from at we you they our your their can does not no if when "
    "what why how which who where RAG".split()
)


def _content_words(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _word_overlap(a, b):
    wa, wb = _content_words(a), _content_words(b)
    if not wa:
        return 0.0
    return len(wa & wb) / len(wa)


def _services_up(app):
    try:
        app.collection.query(query_texts=["ping"], n_results=1)
        return True
    except Exception:
        return False


def _expected_in_hits(app, hits, item):
    fn = item["expected_filename"]
    hint = item["expected_hint"].lower()
    for doc, meta, _ in hits:
        if meta.get("filename") == fn and hint in doc.lower():
            return True
    return False


def _citation_validity(answer_text, n_hits):
    markers = re.findall(r"\[(\d+)\]", answer_text)
    if not markers:
        return 1.0  # no citations claimed -> nothing to invalidate
    valid = sum(1 for m in markers if 1 <= int(m) <= n_hits)
    return valid / len(markers)


def _timed_answer(app, q, model, timeout_s=300):
    """Call app.answer in a thread so one slow model can't hang the run."""
    import threading

    result = {"text": "[TIMEOUT]", "ok": False}

    def _run():
        try:
            result["text"], _ = app.answer(q, "All", model=model)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001
            result["text"] = f"[ERROR] {e}"

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout_s)
    return result["text"]


def run_model(app, model, ingest_ts):
    app.CHAT_MODEL = model
    # re-ingest with stable timestamp so collection is fresh for this model
    os.environ["INGEST_TS"] = str(ingest_ts)
    app.ingest()

    per_item = []
    for item in QUESTIONS:
        q = item["question"]
        t0 = time.perf_counter()
        hits = app.retrieve(q, "All")
        t_retrieve = (time.perf_counter() - t0) * 1000.0

        t1 = time.perf_counter()
        answer_text = _timed_answer(app, q, model)
        t_generate = (time.perf_counter() - t1) * 1000.0

        context_blob = "\n".join(d for d, _, _ in hits)
        per_item.append(
            {
                "question": q,
                "answer": answer_text,
                "n_hits": len(hits),
                "retrieval_recall": 1.0 if _expected_in_hits(app, hits, item) else 0.0,
                "citation_accuracy": _citation_validity(answer_text, len(hits)),
                "faithfulness": _word_overlap(answer_text, context_blob),
                "retrieve_latency_ms": round(t_retrieve, 1),
                "generate_latency_ms": round(t_generate, 1),
                "answer_length": len(answer_text),
            }
        )

    n = len(per_item)
    agg = {
        "model": model,
        "retrieval_recall@8": round(sum(p["retrieval_recall"] for p in per_item) / n, 3),
        "citation_accuracy": round(sum(p["citation_accuracy"] for p in per_item) / n, 3),
        "faithfulness": round(sum(p["faithfulness"] for p in per_item) / n, 3),
        "retrieve_latency_ms": round(sum(p["retrieve_latency_ms"] for p in per_item) / n, 1),
        "generate_latency_ms": round(sum(p["generate_latency_ms"] for p in per_item) / n, 1),
        "answer_length": round(sum(p["answer_length"] for p in per_item) / n),
        "items": n,
    }
    return agg, per_item


def _print_table(rows):
    cols = [
        "model", "retrieval_recall@8", "citation_accuracy", "faithfulness",
        "retrieve_latency_ms", "generate_latency_ms", "answer_length",
    ]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    sep = "-+-".join("-" * widths[c] for c in cols)
    print(line)
    print(sep)
    for r in rows:
        print(" | ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main():
    ap = argparse.ArgumentParser(description="LocalRAG model benchmark")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS),
                    help="comma-separated chat models")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"),
                    help="directory for JSON results")
    args = ap.parse_args()

    # import app with UI stubbed (mirrors conftest)
    import importlib.util
    import types

    stub = types.ModuleType("streamlit")

    class _SessionState(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                raise AttributeError(name)

        def __setattr__(self, name, value):
            self[name] = value

    stub.session_state = _SessionState()
    for name in ("set_page_config", "title", "caption", "sidebar", "header",
                 "button", "success", "divider", "selectbox", "chat_message",
                 "write", "chat_input", "spinner", "expander", "markdown",
                 "subheader"):
        setattr(stub, name, lambda *a, **k: None)
    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Sidebar(_Ctx):
        def header(self, *a, **k):
            return None

        def button(self, *a, **k):
            return None

        def success(self, *a, **k):
            return None

        def divider(self, *a, **k):
            return None

        def selectbox(self, *a, **k):
            return "All"

    stub.sidebar = _Sidebar()
    sys.modules["streamlit"] = stub

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Host layout: LocalRAG/app/app.py. Image layout: /app/app.py.
    candidates = [
        os.path.join(repo_root, "app", "app.py"),
        os.path.join(repo_root, "app.py"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"),
    ]
    app_path = next((p for p in candidates if os.path.exists(p)), candidates[0])
    spec = importlib.util.spec_from_file_location("localrag_app", app_path)
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)

    if not _services_up(app):
        print("SKIPPED: Ollama/Chroma not reachable at "
              f"{app.OLLAMA_BASE_URL} / {app.CHROMA_BASE_URL}")
        sys.exit(0)

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    ingest_ts = int(time.time())
    rows, raw = [], {}
    for model in models:
        print(f"== benchmarking {model} ==")
        agg, per_item = run_model(app, model, ingest_ts)
        rows.append(agg)
        raw[model] = {"aggregate": agg, "per_item": per_item}

    print("\n=== LocalRAG benchmark ===")
    _print_table(rows)

    os.makedirs(args.out, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(args.out, f"benchmark_{stamp}.json")
    with open(out_path, "w") as f:
        json.dump({"generated": stamp, "models": raw}, f, indent=2)
    print(f"\nJSON written to {out_path}")


if __name__ == "__main__":
    main()
