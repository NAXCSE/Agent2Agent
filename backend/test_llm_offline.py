"""Offline test of the provider-agnostic LLM layer.

Uses a stub client so the Gemini-dependent code paths (profile analysis and
agent-to-agent conversations) can be verified without API keys, and proves a
different provider can be swapped in without touching service code.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from pydantic import BaseModel  # noqa: E402

from app.llm import LLMClient, LLMConfigError, create_llm, register_provider  # noqa: E402
from app.llm.json_utils import extract_json  # noqa: E402
from app.models.schemas import AgentPerson  # noqa: E402
from app.services.dating_engine import DatingEngine, DatingEngineError  # noqa: E402
from app.services.pipeline import build_person_profile  # noqa: E402
from app.services.profile_analyzer import ProfileAnalyzer  # noqa: E402

AGENT_PAYLOAD = {
    "name": "Aarav Mehta",
    "gender": "male",
    "age_range": "early 30s",
    "location": "Bengaluru, India",
    "occupation": "Backend Engineer at Flipkart",
    "headline": "Backend Engineer at Flipkart",
    "about": "Backend engineer focused on distributed systems.",
    "interests": ["marathons", "coffee", "chess"],
    "values": ["consistency", "curiosity"],
    "personality_traits": ["analytical", "calm"],
    "looking_for": ["warm", "active lifestyle"],
    "deal_breakers": ["smoking"],
    "conversation_style": "intellectual",
    "summary": "Backend engineer in Bengaluru who runs marathons.",
    "confidence": 0.82,
}

CONVERSATION_PAYLOAD = {
    "turns": [
        {"speaker": "a", "message": "Hi Priya, big fan of long walks too."},
        {"speaker": "b", "message": "Ha, where is your favourite walk so far?"},
        {"speaker": "a", "message": "Cubbon Park, early mornings."},
        {"speaker": "b", "message": "Same. We should go together sometime."},
    ],
    "mutual_interest": True,
    "chemistry_score": 0.82,
    "summary": "Easy conversation with strong mutual interest.",
}


class StubLLM(LLMClient):
    """Records prompts and replays canned responses."""

    provider = "stub"

    def __init__(self, responses: List[Dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.prompts: List[Dict[str, Any]] = []

    @property
    def model(self) -> str:
        return "stub-model"

    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        self.prompts.append(
            {
                "prompt": prompt,
                "schema": schema.__name__ if schema else None,
                "temperature": temperature,
            }
        )
        payload = self.responses.pop(0)
        if schema is not None:
            return schema.model_validate(payload).model_dump()
        return payload


def section(title: str) -> None:
    print()
    print("==============================")
    print(title)
    print("==============================")
    print()


def main() -> int:
    checks: Dict[str, bool] = {}

    section("1. PROFILE ANALYSIS VIA INJECTED CLIENT")
    llm = StubLLM([AGENT_PAYLOAD])
    analyzer = ProfileAnalyzer(llm=llm)

    raw_linkedin = [
        {
            "firstName": "Aarav",
            "lastName": "Mehta",
            "headline": "Backend Engineer at Flipkart",
            "about": "x" * 5000,
            "skills": [{"name": "Python"}],
        }
    ]
    raw_instagram = [
        {"username": "aarav", "biography": "runner", "followersCount": 820}
    ]

    agent = analyzer.analyze(raw_linkedin, raw_instagram)

    call = llm.prompts[0]
    print(f"schema requested : {call['schema']}")
    print(f"temperature      : {call['temperature']}")
    print(f"raw about length in prompt: "
          f"{'truncated' if '...[truncated]' in call['prompt'] else 'NOT truncated'}")
    print(f"name={agent.name} gender={agent.gender.value} "
          f"interests={agent.interests}")
    print(f"dealing with raw payload size: {len(call['prompt'])} chars")

    checks["analyzer asks for AgentPerson schema"] = call["schema"] == "AgentPerson"
    checks["long raw fields are truncated"] = "...[truncated]" in call["prompt"]
    checks["raw payload reaches the prompt"] = "aarav" in call["prompt"]
    checks["gender parsed to enum"] = agent.gender.value == "male"
    checks["interests returned"] = agent.interests == AGENT_PAYLOAD["interests"]

    section("2. PERSON PROFILE FROM RAW + AGENT")
    profile = build_person_profile(
        {"linkedin": raw_linkedin, "instagram": raw_instagram},
        "https://www.linkedin.com/in/aarav-mehta",
        "https://www.instagram.com/aarav",
        agent=agent,
    )
    print(f"name={profile.name} gender={profile.gender.value} "
          f"company={profile.current_company} followers={profile.instagram_followers}")
    print(f"raw.linkedin items preserved: {len(profile.raw.linkedin)} "
          f"(about still full length: "
          f"{len(profile.raw.linkedin[0]['about']) == 5000})")
    checks["gender propagated to PersonProfile"] = profile.gender.value == "male"
    checks["raw payload preserved untouched"] = (
        len(profile.raw.linkedin[0]["about"]) == 5000
    )
    checks["instagram fields mapped"] = profile.instagram_followers == 820

    section("3. AGENTS DATE EACH OTHER VIA INJECTED CLIENT")
    engine = DatingEngine(llm=StubLLM([CONVERSATION_PAYLOAD]), rounds=2)
    male = engine.create_agent(agent)

    female_agent = AgentPerson.model_validate(
        {**AGENT_PAYLOAD, "name": "Priya Nair", "gender": "female"}
    )
    female = engine.create_agent(female_agent)

    conversation = engine.date(male, female)
    for turn in conversation.turns:
        who = male.persona.name if turn.speaker_agent_id == male.agent_id else female.persona.name
        print(f"  {who}: {turn.message}")
    print(f"generated_by={conversation.generated_by} "
          f"chemistry={conversation.chemistry_score} "
          f"mutual_interest={conversation.mutual_interest}")

    checks["conversation came from the LLM"] = conversation.generated_by == "llm"
    checks["all turns mapped to real agent ids"] = all(
        turn.speaker_agent_id in (male.agent_id, female.agent_id)
        for turn in conversation.turns
    )
    checks["chemistry preserved"] = conversation.chemistry_score == 0.82
    checks["system prompt is character-bound"] = (
        "never break character" in male.system_prompt
        and "Aarav" in male.system_prompt
    )

    section("4. SAME-GENDER PAIRING IS REFUSED")
    try:
        engine.date(male, male)
        checks["same-gender refused"] = False
        print("FAIL: same-gender pair accepted")
    except DatingEngineError as exc:
        checks["same-gender refused"] = True
        print(f"OK: {exc}")

    section("4b. SPEAKER ATTRIBUTION IS NOT BLAMED ON ONE AGENT")
    sloppy = {
        "turns": [
            {"speaker": "Priya Nair", "message": "Hi Bill, love the books list."},
            {"speaker": "Bill Gates", "message": "Thanks! What are you reading?"},
            {"speaker": "???", "message": "Mostly essays, mostly at night."},
            {"speaker": "Bill Gates", "message": "Night reading is elite."},
        ],
        "mutual_interest": True,
        "chemistry_score": 0.6,
        "summary": "Speakers labelled with names and garbage.",
    }
    attributed = DatingEngine(llm=StubLLM([sloppy]), rounds=2).date(male, female)
    for turn in attributed.turns:
        who = (
            male.persona.name
            if turn.speaker_agent_id == male.agent_id
            else female.persona.name
        )
        print(f"  {who}: {turn.message}")
    speakers = {turn.speaker_agent_id for turn in attributed.turns}
    checks["both agents speak even with messy labels"] = speakers == {
        male.agent_id,
        female.agent_id,
    }
    checks["strict alternation recovers unknown labels"] = (
        [turn.speaker_agent_id for turn in attributed.turns]
        == [male.agent_id, female.agent_id, male.agent_id, female.agent_id]
    )

    section("4c. GEMINI MODEL FALLBACK ON RETIRED MODEL")
    from app.llm.gemini_client import GeminiClient

    fallback_client = GeminiClient(
        api_key="x", model="gemini-retired-model", fallback_models=["gemini-good"]
    )
    checks["starts on configured model"] = fallback_client.model == "gemini-retired-model"
    fallback_client._advance_model()
    print(f"after one retirement: {fallback_client.model}")
    checks["advances to fallback model"] = fallback_client.model == "gemini-good"

    from app.llm.gemini_client import DEFAULT_FALLBACK_MODELS, DEFAULT_MODEL

    print(f"default model   : {DEFAULT_MODEL}")
    print(f"default fallback: {', '.join(DEFAULT_FALLBACK_MODELS)}")
    checks["default model has fallbacks"] = bool(DEFAULT_FALLBACK_MODELS)

    section("5. PROVIDER SWITCHING (no service code changes)")
    register_provider("stub-provider", lambda model=None: StubLLM([AGENT_PAYLOAD]))
    switched = create_llm("stub-provider")
    print(f"create_llm('stub-provider') -> {type(switched).__name__} "
          f"provider={switched.provider} model={switched.model}")
    checks["custom provider registered"] = switched.provider == "stub"

    for provider in ("gemini", "openai", "anthropic"):
        try:
            client = create_llm(provider)
            print(f"{provider}: ready (model={client.model})")
            checks[f"{provider} builds"] = True
        except LLMConfigError as exc:
            print(f"{provider}: not available -> {exc}")
            checks[f"{provider} builds"] = False

    try:
        create_llm("does-not-exist")
        checks["unknown provider rejected"] = False
    except LLMConfigError as exc:
        print(f"unknown provider -> {exc}")
        checks["unknown provider rejected"] = True

    section("6. JSON EXTRACTION ROBUSTNESS")
    cases = {
        "plain": '{"a": 1}',
        "fenced": '```json\n{"a": 2}\n```',
        "prose wrapped": 'Sure! Here you go: {"a": 3} hope that helps.',
    }
    for label, text in cases.items():
        print(f"{label:<14} -> {extract_json(text)}")
        checks[f"extract json: {label}"] = extract_json(text)["a"] != 0

    section("RESULTS")
    optional = [k for k in checks if k.endswith(" builds")]
    for label, passed in checks.items():
        marker = "INFO" if label in optional else ("PASS" if passed else "FAIL")
        print(f"[{marker}] {label}")
    required = [k for k in checks if k not in optional]
    ok = all(checks[k] for k in required)
    print()
    print("note: live provider checks are informational (need API keys / SDKs)")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())