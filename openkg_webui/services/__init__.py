"""
Services Layer
==============

Unified service layer for OPENKG-WebUI providing:
- LLM client and configuration
- Prompt management
- Web Search providers
- System setup utilities
- Configuration loading

Usage:
    from openkg_webui.services.llm import get_llm_client
    from openkg_webui.services.prompt import get_prompt_manager
    from openkg_webui.services.search import web_search
    from openkg_webui.services.setup import init_user_directories
    from openkg_webui.services.config import load_config_with_main

    # LLM
    llm = get_llm_client()
    response = await llm.complete("Hello, world!")

    # Embedding
    embed = get_embedding_client()
    vectors = await embed.embed(["text1", "text2"])

    # RAG (LlamaIndex backend)
    rag = RAGService()
    result = await rag.search("query", kb_name="my_kb")

    # Prompt
    pm = get_prompt_manager()
    prompts = pm.load_prompts("solve", "solve_agent")

    # Search
    result = web_search("What is AI?")
"""

# Keep service package import side-effects minimal.
# Modules are lazy-loaded in __getattr__ to avoid circular imports.
from .path_service import PathService, get_path_service

__all__ = [
    "llm",
    "prompt",
    "search",
    "setup",
    "session",
    "config",
    "PathService",
    "get_path_service",
]


def __getattr__(name: str):
    """Lazy import for modules that depend on heavy libraries."""
    import importlib

    if name == "llm":
        return importlib.import_module("openkg_webui.services.llm")
    if name == "prompt":
        return importlib.import_module("openkg_webui.services.prompt")
    if name == "search":
        return importlib.import_module("openkg_webui.services.search")
    if name == "setup":
        return importlib.import_module("openkg_webui.services.setup")
    if name == "session":
        return importlib.import_module("openkg_webui.services.session")
    if name == "config":
        return importlib.import_module("openkg_webui.services.config")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
