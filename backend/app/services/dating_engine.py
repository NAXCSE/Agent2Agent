from typing import Dict, List, Optional

from app.llm import LLMClient, create_llm
from app.llm.base import LLMError
from app.models.schemas import (
    AgentPerson,
    Conversation,
    ConversationDraft,
    ConversationTurn,
    DatingAgent,
)
from app.prompts.profile_prompt import (
    AGENT_SYSTEM_PROMPT_TEMPLATE,
    CONVERSATION_PROMPT,
)


class DatingEngineError(RuntimeError):
    """Raised when two agents cannot be paired."""


def _agent_id(person: AgentPerson) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in person.name.lower())
    return f"{person.gender.value}-{slug.strip('-')}"


class DatingEngine:
    """Creates one agent per person and lets two agents talk to each other."""

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        rounds: int = 3,
    ) -> None:
        self._llm = llm
        self.rounds = rounds

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm()
        return self._llm

    def create_agent(self, persona: AgentPerson) -> DatingAgent:
        system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
            name=persona.name,
            gender=persona.gender.value,
            age_range=persona.age_range or "unknown",
            location=persona.location or "unknown",
            occupation=persona.occupation or "unknown",
            headline=persona.headline or "unknown",
            about=persona.about or "unknown",
            interests=", ".join(persona.interests) or "unknown",
            values=", ".join(persona.values) or "unknown",
            personality_traits=", ".join(persona.personality_traits) or "unknown",
            looking_for=", ".join(persona.looking_for) or "unknown",
            deal_breakers=", ".join(persona.deal_breakers) or "none stated",
            conversation_style=persona.conversation_style,
            summary=persona.summary or "unknown",
        )
        return DatingAgent(
            agent_id=_agent_id(persona),
            persona=persona,
            system_prompt=system_prompt,
        )

    def create_agents(self, personas: List[AgentPerson]) -> Dict[str, DatingAgent]:
        return {agent.agent_id: agent for agent in
                (self.create_agent(p) for p in personas)}

    @staticmethod
    def _check_genders(agent_a: DatingAgent, agent_b: DatingAgent) -> None:
        gender_a = agent_a.persona.gender
        gender_b = agent_b.persona.gender

        if gender_a == gender_b:
            raise DatingEngineError(
                f"Cannot pair agents with the same gender: "
                f"{agent_a.agent_id} and {agent_b.agent_id} are both "
                f"{gender_a.value}. Agent2Agent matches male and female only."
            )

    def date(self, agent_a: DatingAgent, agent_b: DatingAgent) -> Conversation:
        self._check_genders(agent_a, agent_b)

        prompt = CONVERSATION_PROMPT.format(
            a_prompt=agent_a.system_prompt,
            b_prompt=agent_b.system_prompt,
            rounds=self.rounds,
        )

        try:
            draft = ConversationDraft.model_validate(
                self.llm.generate_json(
                    prompt,
                    schema=ConversationDraft,
                    temperature=0.8,
                    max_output_tokens=2048,
                )
            )
            return self._to_conversation(agent_a, agent_b, draft, "llm")
        except LLMError as exc:
            return self._fallback_conversation(agent_a, agent_b, str(exc))

    def _to_conversation(
        self,
        agent_a: DatingAgent,
        agent_b: DatingAgent,
        draft: ConversationDraft,
        generated_by: str,
    ) -> Conversation:
        alias_a = {
            "a",
            "agent_a",
            "agent a",
            "0",
            agent_a.agent_id,
            agent_a.persona.name,
        }
        alias_b = {
            "b",
            "agent_b",
            "agent b",
            "1",
            agent_b.agent_id,
            agent_b.persona.name,
        }

        turns: List[ConversationTurn] = []
        expected = agent_a.agent_id

        for turn in draft.turns:
            message = (turn.message or "").strip()
            if not message:
                continue

            speaker = turn.speaker.strip().lower()
            if speaker in alias_a:
                speaker_id = agent_a.agent_id
            elif speaker in alias_b:
                speaker_id = agent_b.agent_id
            else:
                # Unknown label: fall back to strict alternation, which is what
                # the prompt asks for, instead of blaming one agent for
                # everything the other said.
                speaker_id = expected

            turns.append(
                ConversationTurn(speaker_agent_id=speaker_id, message=message)
            )
            expected = (
                agent_b.agent_id if speaker_id == agent_a.agent_id else agent_a.agent_id
            )

        return Conversation(
            agent_a_id=agent_a.agent_id,
            agent_b_id=agent_b.agent_id,
            turns=turns,
            mutual_interest=draft.mutual_interest,
            chemistry_score=max(0.0, min(1.0, draft.chemistry_score)),
            summary=draft.summary,
            generated_by=generated_by,
        )

    def _fallback_conversation(
        self,
        agent_a: DatingAgent,
        agent_b: DatingAgent,
        reason: str,
    ) -> Conversation:
        """Deterministic conversation so the pipeline still runs without an LLM."""
        a, b = agent_a.persona, agent_b.persona
        first_a = a.name.split()[0]
        first_b = b.name.split()[0]

        shared = [
            i for i in a.interests
            if i.lower() in {x.lower() for x in b.interests}
        ]
        a_interest = a.interests[0] if a.interests else "new things"
        b_interest = b.interests[0] if b.interests else "good food and travel"

        def a_line(index: int) -> str:
            if index == 0:
                topic = shared[0] if shared else a_interest
                return (
                    f"Hi {first_b}, I saw we are both into {topic}. "
                    f"What got you into it?"
                )
            if index == 1:
                return (
                    f"Hey {first_b}, {b.occupation or 'your work'} sounds "
                    f"interesting - what is the best part of it?"
                )
            if index == 2:
                return (
                    f"I like people who stay curious about something. "
                    f"What are you into outside work, {first_b}?"
                )
            return f"What else should I know about you, {first_b}?"

        def b_line(index: int) -> str:
            if index == 0:
                return (
                    f"Nice to meet you {first_a}. I am into {b_interest} too - "
                    f"any recommendation?"
                )
            if index == 1:
                return (
                    f"Ha, I like that. Fair warning, my bio says "
                    f"{b.conversation_style}."
                )
            if index == 2:
                return (
                    f"Honestly the best part is {b.summary.split('.')[0].lower()}. "
                    f"Tell me something I would not guess about you?"
                )
            return f"Same here. This has been easy, {first_a}."

        lines: List[ConversationTurn] = []
        for index in range(self.rounds):
            lines.append(
                ConversationTurn(
                    speaker_agent_id=agent_a.agent_id,
                    message=a_line(index),
                )
            )
            lines.append(
                ConversationTurn(
                    speaker_agent_id=agent_b.agent_id,
                    message=b_line(index),
                )
            )

        chemistry = 0.7 if shared else 0.5

        return Conversation(
            agent_a_id=agent_a.agent_id,
            agent_b_id=agent_b.agent_id,
            turns=lines,
            mutual_interest=bool(shared),
            chemistry_score=chemistry,
            summary=(
                f"Fallback conversation (LLM unavailable: {reason}). "
                f"{'Shared interests found.' if shared else 'No shared interests detected.'}"
            ),
            generated_by="fallback",
        )