import os
import time
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel

from app.llm.base import LLMClient, LLMConfigError, LLMError
from app.llm.json_utils import extract_json

DEFAULT_MODEL = "gemini-3.8-flash"

# Free-tier keys lose access to older models (404 "no longer available") and hit
# capacity limits on busy ones (503). Falling through this list keeps the
# pipeline alive instead of failing on a model that went away.
DEFAULT_FALLBACK_MODELS = (
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
)

# Transient conditions worth retrying on the same model.
RETRYABLE_MARKERS = (
    "429",
    "500",
    "502",
    "503",
    "504",
    "RESOURCE_EXHAUSTED",
    "UNAVAILABLE",
)
# Conditions where retrying the same model can never help.
RETIRED_MARKERS = ("404", "no longer available", "NOT_FOUND")

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = (2, 5, 12)


def _split_models(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class GeminiClient(LLMClient):
    """Google Gemini adapter (google-genai SDK)."""

    provider = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        fallback_models: Optional[List[str]] = None,
    ) -> None:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise LLMConfigError(
                "google-genai is not installed. Run: pip install google-genai"
            ) from exc

        self._types = types
        self._client = genai.Client(api_key=api_key)

        self._candidates: List[str] = [model]
        for name in fallback_models or []:
            if name not in self._candidates:
                self._candidates.append(name)
        self._retired: set = set()
        self._index = 0

    @property
    def model(self) -> str:
        return self._candidates[self._index]

    @property
    def candidates(self) -> List[str]:
        return list(self._candidates)

    def _advance_model(self) -> None:
        """Move to the next candidate after a permanent failure."""
        self._retired.add(self._candidates[self._index])
        while self._index < len(self._candidates) - 1:
            self._index += 1
            if self._candidates[self._index] not in self._retired:
                break

    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        config_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "response_mime_type": "application/json",
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if max_output_tokens:
            config_kwargs["max_output_tokens"] = max_output_tokens
        if schema is not None:
            config_kwargs["response_schema"] = schema

        last_error = ""

        for _ in range(len(self._candidates)):
            model = self.model

            for attempt in range(MAX_ATTEMPTS):
                try:
                    response = self._client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=self._types.GenerateContentConfig(**config_kwargs),
                    )
                except Exception as exc:  # noqa: BLE001 - retried / rotated below
                    last_error = str(exc)

                    if any(m in last_error for m in RETIRED_MARKERS):
                        break  # this model is gone for good, rotate immediately

                    if not any(m in last_error for m in RETRYABLE_MARKERS):
                        raise LLMError(f"Gemini request failed: {last_error}") from None

                    if attempt < MAX_ATTEMPTS - 1:
                        delay = BACKOFF_SECONDS[
                            min(attempt, len(BACKOFF_SECONDS) - 1)
                        ]
                        time.sleep(delay)
                    continue

                parsed = getattr(response, "parsed", None)
                if schema is not None and parsed is not None:
                    return parsed.model_dump()

                text = getattr(response, "text", "") or ""
                data = extract_json(text)
                if schema is not None:
                    from app.llm.json_utils import coerce_to_schema

                    return coerce_to_schema(data, schema)
                return data if isinstance(data, dict) else {"result": data}

            # Out of attempts on this model: try the next candidate.
            if self._index < len(self._candidates) - 1:
                previous = self.model
                self._advance_model()
                last_error = f"{last_error} (rotated {previous} -> {self.model})"
                continue

            break

        raise LLMError(
            f"Gemini request failed on all of "
            f"{', '.join(self.candidates)}: {last_error}"
        )