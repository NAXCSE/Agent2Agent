"""Provider registry.

Adding a provider = write an LLMClient adapter + register it here.
No service code changes are needed.
"""

import os
from pathlib import Path
from typing import Callable, Dict, Optional

from dotenv import load_dotenv

from app.llm.base import LLMClient, LLMConfigError

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"

_PLACEHOLDER_PREFIXES = ("your_", "paste_", "changeme", "xxx", "<", "todo", "replace")

_REGISTRY: Dict[str, Callable[[], LLMClient]] = {}


def register_provider(name: str, factory: Callable[[], LLMClient]) -> None:
    _REGISTRY[name] = factory


def _load_env() -> None:
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE)


def _require_env(name: str, provider: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise LLMConfigError(
            f"Missing {name} for provider '{provider}'. Add it to {ENV_FILE}."
        )
    if value.lower().startswith(_PLACEHOLDER_PREFIXES):
        raise LLMConfigError(
            f"{name} for provider '{provider}' still holds a placeholder value."
        )
    return value


def available_providers() -> list[str]:
    return sorted(_REGISTRY)


def create_llm(
    provider: Optional[str] = None,
    *,
    model: Optional[str] = None,
) -> LLMClient:
    """Build a client from environment configuration.

    LLM_PROVIDER selects the provider. A specific client can also be passed in
    directly to services, which is what tests and the API layer do.
    """
    _load_env()

    provider = (provider or os.getenv("LLM_PROVIDER") or "gemini").strip().lower()

    if provider not in _REGISTRY:
        raise LLMConfigError(
            f"Unknown LLM provider '{provider}'. "
            f"Available: {', '.join(available_providers()) or 'none'}."
        )

    return _REGISTRY[provider](model)


def _build_gemini(model: Optional[str]) -> LLMClient:
    from app.llm.gemini_client import (
        DEFAULT_FALLBACK_MODELS,
        DEFAULT_MODEL,
        GeminiClient,
    )

    fallbacks = [
        item.strip()
        for item in (os.getenv("GEMINI_FALLBACK_MODELS") or "").split(",")
        if item.strip()
    ] or list(DEFAULT_FALLBACK_MODELS)

    return GeminiClient(
        api_key=_require_env("GEMINI_API_KEY", "gemini"),
        model=model or os.getenv("GEMINI_MODEL") or DEFAULT_MODEL,
        fallback_models=fallbacks,
    )


def _build_openai(model: Optional[str]) -> LLMClient:
    from app.llm.openai_client import OpenAICompatibleClient

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY") or ""
    base_url = os.getenv("OPENAI_BASE_URL") or ""

    if not api_key:
        if not base_url:
            raise LLMConfigError(
                f"Missing OPENAI_API_KEY for provider 'openai'. Add it to "
                f"{ENV_FILE}, or set OPENAI_BASE_URL for a local server."
            )
        # Local servers (Ollama, LM Studio, vLLM) accept any non-empty key.
        api_key = "local"

    return OpenAICompatibleClient(
        api_key=api_key,
        model=model or os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        base_url=base_url or None,
    )


def _build_anthropic(model: Optional[str]) -> LLMClient:
    from app.llm.anthropic_client import AnthropicClient

    return AnthropicClient(
        api_key=_require_env("ANTHROPIC_API_KEY", "anthropic"),
        model=model or os.getenv("ANTHROPIC_MODEL") or "claude-sonnet-4-5",
    )


register_provider("gemini", lambda model=None: _build_gemini(model))
register_provider("openai", lambda model=None: _build_openai(model))
register_provider(
    "openai-compatible", lambda model=None: _build_openai(model)
)
register_provider("anthropic", lambda model=None: _build_anthropic(model))