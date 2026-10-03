import json
from typing import Any, Dict, List, Optional

from app.llm import LLMClient, create_llm
from app.llm.base import LLMError
from app.models.schemas import AgentPerson, RawPerson
from app.prompts.profile_prompt import (
    PROFILE_ANALYSIS_INSTRUCTION,
    PROFILE_ANALYSIS_PROMPT,
)

MAX_FIELD_CHARS = 2000


class ProfileAnalysisError(RuntimeError):
    """Raised when a person cannot be turned into an AI profile."""


def _truncate(value: Any) -> Any:
    """Keep the raw payload small enough for a single prompt.

    Only long strings are shortened. Keys and structure are untouched, so the
    full raw payload is still available on PersonProfile.raw.
    """
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + "...[truncated]"
    if isinstance(value, dict):
        return {k: _truncate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_truncate(v) for v in value]
    return value


def _dump(items: List[Dict[str, Any]]) -> str:
    return json.dumps(_truncate(items), indent=2, ensure_ascii=False, default=str)


class ProfileAnalyzer:
    """Turns raw Apify output into an AgentPerson using any LLM provider."""

    def __init__(self, llm: Optional[LLMClient] = None) -> None:
        self._llm = llm

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm()
        return self._llm

    def analyze(
        self,
        linkedin_items: List[Dict[str, Any]],
        instagram_items: List[Dict[str, Any]],
    ) -> AgentPerson:
        if not linkedin_items and not instagram_items:
            raise ProfileAnalysisError(
                "Profile analysis failed: no scraped data to analyze."
            )

        prompt = PROFILE_ANALYSIS_PROMPT.format(
            linkedin_data=_dump(linkedin_items),
            instagram_data=_dump(instagram_items),
        )
        prompt = f"{prompt}\n\n{PROFILE_ANALYSIS_INSTRUCTION}"

        try:
            data = self.llm.generate_json(
                prompt,
                schema=AgentPerson,
                temperature=0.2,
                max_output_tokens=2048,
            )
        except LLMError as exc:
            raise ProfileAnalysisError(f"Profile analysis failed: {exc}") from None

        try:
            return AgentPerson.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise ProfileAnalysisError(
                f"Profile analysis failed: invalid agent profile ({exc})"
            ) from None

    def analyze_raw(self, raw: RawPerson) -> AgentPerson:
        return self.analyze(raw.linkedin, raw.instagram)