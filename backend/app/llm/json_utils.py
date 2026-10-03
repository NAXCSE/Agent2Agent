import json
import re
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, ValidationError

from app.llm.base import LLMError

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def extract_json(text: str) -> Any:
    """Pull a JSON object out of a model response.

    Models sometimes wrap JSON in prose or ```json fences; this strips both
    without making every provider adapter repeat the logic.
    """
    if not text:
        raise LLMError("LLM returned an empty response.")

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"```$", "", cleaned).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(cleaned)
        if not match:
            raise LLMError(f"LLM response was not valid JSON: {cleaned[:200]}") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise LLMError(f"LLM response was not valid JSON: {exc}") from None


def coerce_to_schema(data: Any, schema: Type[BaseModel]) -> Dict[str, Any]:
    """Validate a decoded payload against a pydantic schema."""
    if not isinstance(data, dict):
        raise LLMError(f"Expected a JSON object, got {type(data).__name__}.")

    try:
        return schema.model_validate(data).model_dump()
    except ValidationError as exc:
        raise LLMError(f"LLM response failed schema validation: {exc}") from None


class JsonSchemaMixin:
    """Shared JSON-mode helpers for adapters that talk raw HTTP-ish APIs."""

    def _request_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]],
        system_instruction: Optional[str],
        temperature: float,
        max_output_tokens: Optional[int],
        text: str,
    ) -> Dict[str, Any]:
        data = extract_json(text)
        if schema is not None:
            return coerce_to_schema(data, schema)
        return data if isinstance(data, dict) else {"result": data}