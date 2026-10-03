from typing import Any, Dict, Optional, Type

from pydantic import BaseModel

from app.llm.base import LLMClient, LLMConfigError, LLMError
from app.llm.json_utils import extract_json

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleClient(LLMClient):
    """Adapter for any OpenAI-compatible /v1/chat/completions endpoint.

    Covers OpenAI, Groq, Together, OpenRouter, DeepSeek, Fireworks, vLLM,
    LM Studio and Ollama (via its OpenAI shim) by changing base_url/model.
    """

    provider = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - dependency missing
            raise LLMConfigError(
                "openai is not installed. Run: pip install openai"
            ) from exc

        self._model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)

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
        messages: list[Dict[str, str]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        request: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_output_tokens:
            request["max_tokens"] = max_output_tokens

        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            }
        else:
            request["response_format"] = {"type": "json_object"}

        try:
            response = self._client.chat.completions.create(**request)
            text = response.choices[0].message.content or ""
        except Exception as exc:  # noqa: BLE001 - normalized into LLMError
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from None

        data = extract_json(text)
        if schema is not None:
            from app.llm.json_utils import coerce_to_schema

            return coerce_to_schema(data, schema)
        return data if isinstance(data, dict) else {"result": data}