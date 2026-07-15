# Sample notes

This is a seed document for the Local RAG Notebook lab. It is split into
themed sections so the structure-aware chunker and the hybrid retriever have
real signal to work with during tests and benchmarks.

## What RAG is

RAG (Retrieval-Augmented Generation) keeps the language model honest by
grounding its answers in your own documents instead of its training data.
A retriever finds the most relevant passages, and a generator answers using
only that context, citing the source.

## The offline stack

A local stack means nothing leaves your machine: the embeddings and the
chat both run on Ollama, and the vector store lives in Chroma on your own
disk. Replace this file with your own notes, PDFs, or markdown and re-run
ingest to ask questions about them.

## Ollama

Ollama serves the models. The embedding model turns text into vectors and
the chat model answers questions. Both run as containers in this lab, and
once pulled they work with no internet connection.

## Chroma

Chroma is the vector store. It keeps the embedded chunks on disk and fuses
lexical (BM25) search with semantic (vector) search to retrieve the chunks
that best match a question, whether the match is an exact keyword or a
meaning.
