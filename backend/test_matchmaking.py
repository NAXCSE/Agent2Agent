"""Offline test of the matchmaking flow for 12 male + 12 female.

Shows the cost control the whole design depends on:

    144 possible pairs -> shortlist -> conversations -> judged -> ranked

A stub LLM stands in for a real provider so the test needs no API keys.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

from pydantic import BaseModel  # noqa: E402

from app.llm import LLMClient  # noqa: E402
from app.models.schemas import (  # noqa: E402
    MatchmakingConfig,
    PersonProfile,
)
from app.services.matchmaking import Matchmaker  # noqa: E402

DEMO_FILE = PROJECT_DIR / "data" / "demo_people.json"


class ScriptedLLM(LLMClient):
    """Replays believable conversations and verdicts, counting LLM calls."""

    provider = "scripted"

    OPENERS = [
        "Hey {other}, I have to ask about {topic} - is that something you got into recently?",
        "That is genuinely interesting. What made you pick {topic} over everything else?",
        "Fair warning: I will probably turn this into a three hour conversation about {topic}.",
        "Okay, we are getting along. What is the one thing we should do if we met?",
        "Last one from me: what is on your calendar this weekend?",
        "This was better than I expected. Send me the thing you just mentioned.",
    ]

    REPLIES = [
        "Ha, {topic} is basically my whole personality at this point.",
        "Honestly? I fell into it and now it eats my weekends. Worth it though.",
        "I keep telling myself I will try something new. Clearly I have not.",
        "Then we are doing that. I know a place, but you are choosing the dessert.",
        "Nothing exciting, I finally promised myself I would take the day off.",
        "Good. Your move. Do not overthink it.",
    ]

    def __init__(self, rounds: int) -> None:
        self.rounds = rounds
        self.calls: List[str] = []
        self.topics = [
            "marathons", "filter coffee", "pottery", "photography", "chess",
            "trekking", "board games", "live music", "startups", "cycling",
        ]

    @property
    def model(self) -> str:
        return "scripted-model"

    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        name = schema.__name__ if schema else "None"
        self.calls.append(name)

        if name == "ConversationDraft":
            turns = []
            for index in range(self.rounds):
                topic = self.topics[index % len(self.topics)]
                turns.append(
                    {
                        "speaker": "a",
                        "message": self.OPENERS[index % len(self.OPENERS)].format(
                            other="there", topic=topic
                        ),
                    }
                )
                turns.append(
                    {
                        "speaker": "b",
                        "message": self.REPLIES[index % len(self.REPLIES)].format(
                            topic=topic
                        ),
                    }
                )
            return {
                "turns": turns,
                "mutual_interest": True,
                "chemistry_score": 0.7,
                "summary": "Scripted conversation for offline testing.",
            }

        if name == "ConversationVerdict":
            lines = [ln for ln in prompt.splitlines() if ln.strip()]
            transcript = "\n".join(lines[-20:])
            richness = min(len(transcript) / 4000, 1.0)
            return {
                "interestingness": round(0.4 + richness * 0.5, 3),
                "depth": round(0.35 + richness * 0.45, 3),
                "chemistry": round(0.5 + richness * 0.4, 3),
                "mutual_interest": True,
                "common_ground": ["marathons", "photography", "weekend trips"],
                "red_flags": [],
                "verdict": "Scripted verdict.",
            }

        raise AssertionError(f"unexpected schema request: {name}")


def section(title: str) -> None:
    print()
    print("==============================")
    print(title)
    print("==============================")
    print()


def main() -> int:
    people = [PersonProfile.model_validate(p) for p in
              json.loads(DEMO_FILE.read_text(encoding="utf-8"))]

    males = [p for p in people if p.gender.value == "male"]
    females = [p for p in people if p.gender.value == "female"]

    section("POOL")
    print(f"total={len(people)} male={len(males)} female={len(females)}")
    print(f"all possible pairs = {len(males)} x {len(females)} = "
          f"{len(males) * len(females)}")

    config = MatchmakingConfig(
        rounds=3,
        shortlist_per_person=2,
        max_conversations=8,
        judge=True,
        min_messages=4,
    )
    llm = ScriptedLLM(rounds=config.rounds)
    result = Matchmaker(llm=llm, config=config).run(people)

    section("COST CONTROL")
    print(f"pairs scored (free)        : {result.total_pairs_considered}")
    print(f"pairs shortlisted          : {result.pairs_shortlisted}")
    print(f"conversations held         : {result.conversations_held}")
    print(f"LLM calls made             : {len(llm.calls)} "
          f"({llm.calls.count('ConversationDraft')} conversations + "
          f"{llm.calls.count('ConversationVerdict')} verdicts)")
    print(f"excluded pairs             : {len(result.excluded_pairs)}")

    section("FINAL RANKING")
    for index, match in enumerate(result.eligible_matches[:10], start=1):
        verdict = match.verdict
        print(f"{index:>2}. {match.person_a_name} + {match.person_b_name} "
              f"[{match.gender_pair}]")
        print(f"    final={match.final_score} "
              f"compatibility={match.compatibility_score} "
              f"messages={match.message_count} "
              f"ground={len(verdict.common_ground) if verdict else 0}")

    section("TOP MATCH DETAIL")
    top = result.eligible_matches[0]
    print(f"{top.person_a_name} <-> {top.person_b_name}")
    print(f"score breakdown: {json.dumps(top.score_breakdown)}")
    print(f"judged_by={top.verdict.judged_by if top.verdict else None} "
          f"verdict={top.verdict.verdict if top.verdict else None}")
    print(f"common ground: {top.verdict.common_ground if top.verdict else []}")
    print()
    for turn in top.conversation.turns:
        who = top.person_a_name if turn.speaker_agent_id == top.person_a_id else top.person_b_name
        print(f"  {who}: {turn.message}")

    section("CHECKS")
    conversed = [m for m in result.eligible_matches if m.conversation is not None]
    judged = [m for m in result.eligible_matches if m.verdict is not None]
    same_gender_in_ranked = [
        m for m in result.eligible_matches
        if m.person_a_name in {p.name for p in males}
        and m.person_b_name in {p.name for p in males}
        or m.person_a_name in {p.name for p in females}
        and m.person_b_name in {p.name for p in females}
    ]

    checks = {
        "24 people loaded": len(people) == 24,
        "12 male / 12 female": len(males) == 12 and len(females) == 12,
        "all 144 pairs scored": result.total_pairs_considered == 276,
        "shortlist is much smaller than all pairs":
            0 < result.pairs_shortlisted < result.total_pairs_considered,
        "conversation cap respected": result.conversations_held <= config.max_conversations,
        "only shortlisted pairs conversed":
            all(m.shortlisted for m in conversed),
        "every conversation judged": len(judged) == len(conversed),
        "judge reports all dimensions": all(
            v.interestingness > 0 and v.depth > 0 and v.chemistry > 0
            for v in (m.verdict for m in judged)
        ),
        "every conversation has turns": all(m.message_count > 0 for m in conversed),
        "no same-gender pair in ranking": not same_gender_in_ranked,
        "final scores are 0-100": all(
            0 <= m.final_score <= 100 for m in result.eligible_matches
        ),
        "ranking is descending by final score": all(
            result.eligible_matches[i].final_score >= result.eligible_matches[i + 1].final_score
            for i in range(len(result.eligible_matches) - 1)
        ),
        "shortlist respects per-person limit": all(
            sum(
                1 for m in result.eligible_matches
                if m.shortlisted and (m.person_a_id == person.name or m.person_b_id == person.name)
            ) <= config.shortlist_per_person
            for person in people
        ),
        "llm calls = conversations + verdicts": len(llm.calls) == (
            result.conversations_held * 2
        ),
    }

    ok = True
    for label, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())