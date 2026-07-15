"""Pytest fixtures and environment wiring for the LocalRAG test suite.

Inside the docker-compose network the app must reach Ollama at
``http://ollama:11434`` and Chroma at ``http://chroma:8000``. Run locally
against a host stack, those become ``localhost``. We default to the compose
hostnames and fall back to localhost so the same tests work in both places.

The benchmark talks to live Ollama/Chroma and is marked ``benchmark`` so it
can be excluded from fast test runs::

    pytest -m "not benchmark"
"""

import os
import sys
import types

import pytest


def _in_compose() -> bool:
    return os.environ.get("LOCALRAG_COMPOSE", "").lower() in ("1", "true")


# Reach the services. Compose service names take precedence; localhost is the
# fallback for a host-run stack.
OLLAMA_HOST = "ollama" if _in_compose() else "localhost"
CHROMA_HOST = "chroma" if _in_compose() else "localhost"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", f"http://{OLLAMA_HOST}:11434")
CHROMA_BASE_URL = os.getenv("CHROMA_BASE_URL", f"http://{CHROMA_HOST}:8000")


@pytest.fixture(scope="session", autouse=True)
def _wire_env():
    """Point the app at the right service hosts for the whole session."""
    os.environ["OLLAMA_BASE_URL"] = OLLAMA_BASE_URL
    os.environ["CHROMA_BASE_URL"] = CHROMA_BASE_URL
    os.environ["DOCS_DIR"] = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "docs"
    )
    yield


@pytest.fixture(scope="session")
def streamlit_stub():
    """Stub the streamlit module so app.py imports without launching the UI.

    app.py imports streamlit at module level and calls st.* at import time.
    Tests only need the pure functions (chunking, ingest, retrieve, answer),
    so we register a no-op stub in sys.modules before importing app.
    """
    stub = types.ModuleType("streamlit")
    stub.session_state = {}
    for name in (
        "set_page_config", "title", "caption", "sidebar", "header", "button",
        "success", "divider", "selectbox", "chat_message", "write", "chat_input",
        "spinner", "expander", "markdown", "caption",
    ):
        setattr(stub, name, lambda *a, **k: None)
    # Attribute-style access for `with st.sidebar:` etc.
    stub.sidebar = types.SimpleNamespace(
        header=lambda *a, **k: None,
        button=lambda *a, **k: None,
        success=lambda *a, **k: None,
        divider=lambda *a, **k: None,
        selectbox=lambda *a, **k: "All",
    )
    sys.modules["streamlit"] = stub
    yield stub
    if "streamlit" in sys.modules and sys.modules["streamlit"] is stub:
        # Leave the stub; re-imports are cheap and consistent.
        pass


@pytest.fixture(scope="session")
def app_module(streamlit_stub):
    """Import the lab app with pure functions accessible, UI stubbed out."""
    import importlib.util
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "app.py"
    spec = importlib.util.spec_from_file_location("localrag_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
