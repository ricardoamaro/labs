"""Test corpus for the LocalRAG benchmark.

Each item describes one question to ask against the ingested `docs/` set and
the evidence we expect the retriever to find. The benchmark uses these to
compute retrieval_recall@8 and to sanity-check citation grounding.

Schema (one dict per item):
    question          -- text sent to retrieve()/answer()
    expected_filename -- the document the gold answer should come from
    expected_hint     -- a lowercase keyword/phrase that must appear in the
                         expected source chunk, used as a soft check that the
                         retriever surfaced the right passage

Add items by appending to QUESTIONS. Keep them answerable from sample.md so
the benchmark runs without external data.
"""

QUESTIONS = [
    {
        "question": "What does RAG stand for and how does it keep a model honest?",
        "expected_filename": "sample.md",
        "expected_hint": "retrieval-augmented generation",
    },
    {
        "question": "Why does a local stack mean nothing leaves my machine?",
        "expected_filename": "sample.md",
        "expected_hint": "nothing leaves your machine",
    },
    {
        "question": "What does Ollama do in this lab?",
        "expected_filename": "sample.md",
        "expected_hint": "ollama serves the models",
    },
    {
        "question": "What is Chroma and what does it store?",
        "expected_filename": "sample.md",
        "expected_hint": "vector store",
    },
    {
        "question": "How does the retriever match a question to chunks?",
        "expected_filename": "sample.md",
        "expected_hint": "lexical",
    },
    {
        "question": "Can the lab work without an internet connection?",
        "expected_filename": "sample.md",
        "expected_hint": "no internet connection",
    },
    {
        "question": "What model turns text into vectors?",
        "expected_filename": "sample.md",
        "expected_hint": "embedding model",
    },
    {
        "question": "What does the chat model do with the retrieved context?",
        "expected_filename": "sample.md",
        "expected_hint": "answers using only that context",
    },
]
