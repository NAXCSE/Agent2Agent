import re
from typing import List, Optional, Set

from app.llm import LLMClient, create_llm
from app.llm.base import LLMError
from app.models.schemas import Conversation, ConversationVerdict, DatingAgent
from app.prompts.profile_prompt import JUDGE_PROMPT

GENERIC_PHRASES = {
    "hi",
    "hello",
    "hey",
    "how are you",
    "nice to meet you",
    "good morning",
    "good evening",
}

STOP_WORDS = {
    "the", "and", "a", "an", "to", "of", "in", "for", "with", "on", "is", "are",
    "you", "your", "i", "my", "me", "we", "it", "that", "this", "at", "be",
    "so", "but", "if", "do", "did", "have", "has", "not", "no", "yes", "hi",
    "hey", "hello", "thanks", "thank", "about", "from", "what", "how",
}


def _content_tokens(message: str) -> Set[str]:
    words = re.findall(r"[a-z']+", message.lower())
    return {
        w for w in words
        if len(w) > 3 and w not in STOP_WORDS
    }


def _transcript(conversation: Conversation, names: dict) -> str:
    lines = []
    for turn in conversation.turns:
        lines.append(f"{names.get(turn.speaker_agent_id, turn.speaker_agent_id)}: {turn.message}")
    return "\n".join(lines)


class ConversationJudge:
    """Scores every conversation on interestingness, depth and common ground."""

    def __init__(self, llm: Optional[LLMClient] = None, strict: bool = True) -> None:
        self._llm = llm
        self.strict = strict

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = create_llm()
        return self._llm

    def judge(
        self,
        agent_a: DatingAgent,
        agent_b: DatingAgent,
        conversation: Conversation,
    ) -> ConversationVerdict:
        names = {
            agent_a.agent_id: agent_a.persona.name,
            agent_b.agent_id: agent_b.persona.name,
        }

        prompt = JUDGE_PROMPT.format(
            a_name=agent_a.persona.name,
            a_gender=agent_a.persona.gender.value,
            a_prompt=agent_a.system_prompt,
            b_name=agent_b.persona.name,
            b_gender=agent_b.persona.gender.value,
            b_prompt=agent_b.system_prompt,
            transcript=_transcript(conversation, names),
        )

        try:
            data = self.llm.generate_json(
                prompt,
                schema=ConversationVerdict,
                temperature=0.1,
                max_output_tokens=1200,
            )
            return ConversationVerdict.model_validate(data).model_copy(
                update={"judged_by": "llm"}
            )
        except LLMError as exc:
            return self.heuristic_judgement(conversation, str(exc))

    def heuristic_judgement(
        self,
        conversation: Conversation,
        reason: str = "",
    ) -> ConversationVerdict:
        """Offline fallback so the pipeline still produces ranked results."""
        turns = conversation.turns
        messages = [turn.message for turn in turns]

        by_agent: dict = {}
        for turn in turns:
            by_agent.setdefault(turn.speaker_agent_id, []).append(turn.message)

        agent_ids = sorted(by_agent)
        first_tokens = _content_tokens(" ".join(by_agent.get(agent_ids[0], [])))
        second_tokens = _content_tokens(" ".join(by_agent.get(agent_ids[1], [])))
        overlap = first_tokens & second_tokens

        generic = sum(
            1 for message in messages
            if message.strip().lower().rstrip(".!") in GENERIC_PHRASES
        )
        questions = sum(1 for message in messages if "?" in message)
        repeats = len(messages) - len({m.strip().lower() for m in messages})

        length_score = min(1.0, len(messages) / 12)
        substance = min(1.0, len(overlap) / 5)
        question_score = min(1.0, questions / max(1, len(messages) / 2))
        penalty = (generic * 0.15) + (repeats * 0.1)

        interestingness = max(
            0.0, min(1.0, 0.35 + substance * 0.4 + question_score * 0.25 - penalty)
        )
        depth = max(
            0.0, min(1.0, 0.25 + length_score * 0.4 + substance * 0.35 - penalty)
        )
        chemistry = max(
            0.0, min(1.0, 0.3 + substance * 0.5 + (0.1 if questions else 0.0) - penalty)
        )

        common_ground: List[str] = sorted(overlap)[:6]

        return ConversationVerdict(
            interestingness=round(interestingness, 3),
            depth=round(depth, 3),
            chemistry=round(chemistry, 3),
            mutual_interest=conversation.mutual_interest,
            common_ground=common_ground,
            red_flags=[],
            verdict=(
                f"Fallback judgement (no LLM available{': ' + reason if reason else ''}). "
                f"{len(messages)} messages, {len(common_ground)} shared topics."
            ),
            judged_by="fallback",
        )