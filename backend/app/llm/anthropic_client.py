from typing import Any, Dict, Optional, Type

from pydantic import BaseModel

from app.llm.base import LLMClient, LLMConfigError, LLMError
from app.llm.json_utils import extract_json

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 2048


class AnthropicClient(LLMClient):
    """Anthropic Claude adapter."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise LLMConfigError(
                "anthropic is not installed. Run: pip install anthropic"
            ) from exc

        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key)

    @property
    def model(self) -> str:
        return self._model

    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        request: Dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_output_tokens or DEFAULT_MAX_TOKENS,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            request["system"] = system_instruction

        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "schema": schema.model_json_schema(),
            }

        try:
            response = self._client.messages.create(**request)
            text = "".join(
                block.text for block in response.content if block.type == "text"
            )
        except Exception as exc:  # noqa: BLE001 - normalized into LLMError
            raise LLMError(f"Anthropic request failed: {exc}") from None

        data = extract_json(text)
        if schema is not None:
            from app.llm.json_utils import coerce_to_schema

            return coerce_to_schema(data, schema)
        return data if isinstance(data, dict) else {"result": data}