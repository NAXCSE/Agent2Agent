from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel


class LLMError(RuntimeError):
    """Raised when an LLM provider call fails."""


class LLMConfigError(LLMError):
    """Raised when a provider is misconfigured or its SDK is missing."""


class LLMClient(ABC):
    """Minimal provider-agnostic interface.

    Every provider adapter implements this, so services never import a
    vendor SDK directly. Swapping providers is a config change, not a code
    change.
    """

    #: provider key, e.g. "gemini" or "openai"
    provider: str = "unknown"

    @property
    @abstractmethod
    def model(self) -> str:
        """Model identifier currently in use."""

    @abstractmethod
    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Return a JSON object.

        When `schema` is given, the result is validated into that pydantic
        model and returned as its `model_dump()`.
        """

    def generate_text(
        self,
        prompt: str,
        *,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
    ) -> str:
        """Return plain text. Defaults to a JSON call rendered as text."""
        result = self.generate_json(
            prompt,
            system_instruction=system_instruction,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        if isinstance(result, str):
            return result
        return str(result)