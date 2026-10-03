"""Offline tests for the pre-conversation age gate and empty-result handling."""

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
    AgentPerson,
    MatchmakingConfig,
    PersonProfile,
)
from app.services.matchmaking import Matchmaker  # noqa: E402
from app.services.ranking_engine import RankingEngine, age_gap, age_midpoint  # noqa: E402


class CountingLLM(LLMClient):
    """Records how many conversations and judgements were actually requested."""

    provider = "counting"

    def __init__(self) -> None:
        self.conversations = 0
        self.judgements = 0

    @property
    def model(self) -> str:
        return "counting-model"

    def generate_json(
        self,
        prompt: str,
        *,
        schema: Optional[Type[BaseModel]] = None,
        system_instruction: Optional[str] = None,
        temperature: float = 0.3,
        max_output_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        if schema is not None and schema.__name__ == "ConversationDraft":
            self.conversations += 1
            return {
                "turns": [
                    {"speaker": "a", "message": "Hi, big fan of marathons too."},
                    {"speaker": "b", "message": "Same, I run on weekends."},
                ],
                "mutual_interest": True,
                "chemistry_score": 0.7,
                "summary": "Counted conversation.",
            }
        if schema is not None and schema.__name__ == "ConversationVerdict":
            self.judgements += 1
            return {
                "interestingness": 0.7,
                "depth": 0.6,
                "chemistry": 0.7,
                "mutual_interest": True,
                "common_ground": ["marathons"],
                "red_flags": [],
                "verdict": "Counted verdict.",
            }
        raise AssertionError(f"unexpected schema: {schema}")


def person(name: str, gender: str, age: str) -> PersonProfile:
    agent = AgentPerson(
        name=name,
        gender=gender,
        age_range=age,
        location="Bengaluru, India",
        occupation="Engineer",
        interests=["marathons", "coffee", "chess"],
        values=["curiosity"],
        personality_traits=["calm"],
        conversation_style="friendly",
        summary=f"{name} demo profile.",
    )
    return PersonProfile(
        name=name,
        gender=gender,
        linkedin_url=f"https://www.linkedin.com/in/{name.lower()}",
        instagram_url=f"https://www.instagram.com/{name.lower()}",
        agent=agent,
    )


def section(title: str) -> None:
    print()
    print("==============================")
    print(title)
    print("==============================")
    print()


def main() -> int:
    checks: Dict[str, bool] = {}

    section("1. AGE PHRASE PARSING")
    cases = {
        "late 60s": 68.0,
        "early 30s": 32.0,
        "mid 30s": 35.0,
        "29s": 29.0,
        "20s": 24.5,
        "45": 45.0,
        "": None,
        "unknown": None,
    }
    for phrase, expected in cases.items():
        got = age_midpoint(phrase)
        ok = (got == expected) if expected is not None else got is None
        print(f"  {phrase or '<empty>':<12} -> {got}   {'ok' if ok else 'EXPECTED ' + str(expected)}")
        checks[f"age parse {phrase or 'empty'}"] = ok

    old_gap = age_gap(
        AgentPerson(name="A", gender="male", age_range="late 60s"),
        AgentPerson(name="B", gender="female", age_range="29s"),
    )
    print(f"  late 60s vs 29s gap -> {old_gap}")
    checks["late 60s gap computed from midpoint"] = old_gap == 39.0

    section("2. AGE GATE DROPS PAIR BEFORE ANY CONVERSATION")
    pool = [
        person("Older Man", "male", "late 60s"),
        person("Young Woman", "female", "29s"),
    ]
    llm = CountingLLM()
    strict = Matchmaker(
        llm=llm, config=MatchmakingConfig(max_age_gap=15, max_conversations=5)
    ).run(pool)
    print(f"  pairs considered : {strict.total_pairs_considered}")
    print(f"  eligible matches : {len(strict.eligible_matches)}")
    print(f"  conversations    : {strict.conversations_held}")
    print(f"  exclusion summary: {strict.exclusion_summary}")
    for match in strict.excluded_pairs:
        for reason in match.rejection_reasons:
            print(f"    excluded: {reason}")
    for hint in strict.suggestions:
        print(f"    hint: {hint}")

    checks["age gate excludes the pair"] = not strict.eligible_matches
    checks["no conversation was spent"] = strict.conversations_held == 0
    checks["llm never called"] = llm.conversations == 0 and llm.judgements == 0
    checks["exclusion counted as age_gap"] = (
        strict.exclusion_summary.get("age_gap") == 1
    )
    checks["a suggestion is offered"] = bool(strict.suggestions)
    checks["no exception on empty result"] = strict.total_pairs_considered == 1

    section("3. SAME GENDER STILL EXCLUDED")
    pool = [
        person("Man A", "male", "early 30s"),
        person("Man B", "male", "early 30s"),
        person("Woman A", "female", "early 30s"),
    ]
    mixed = Matchmaker(
        llm=CountingLLM(), config=MatchmakingConfig(max_age_gap=15)
    ).run(pool)
    print(f"  considered={mixed.total_pairs_considered} "
          f"eligible={len(mixed.eligible_matches)} "
          f"summary={mixed.exclusion_summary}")
    checks["same gender counted"] = mixed.exclusion_summary.get("same_gender") == 1
    # 3 people = 3 pairs: one male/male dropped, two male/female survive.
    checks["both male/female pairs survive"] = len(mixed.eligible_matches) == 2
    checks["surviving pairs are male -> female"] = all(
        match.gender_pair == "male -> female" for match in mixed.eligible_matches
    )

    section("4. GATE DISABLED FALLS BACK TO SCORING")
    relaxed = Matchmaker(
        llm=CountingLLM(), config=MatchmakingConfig(max_age_gap=None)
    ).run([person("Older Man", "male", "late 60s"),
           person("Young Woman", "female", "29s")])
    top = relaxed.eligible_matches[0]
    print(f"  eligible={len(relaxed.eligible_matches)} conversations={relaxed.conversations_held}")
    print(f"  age factor: {[f.reason for f in top.factors if f.name == 'age']}")
    checks["pair survives when gate disabled"] = bool(relaxed.eligible_matches)
    checks["age still scored as a factor"] = any(
        f.name == "age" and f.score <= 0.5 for f in top.factors
    )

    section("5. DEAL BREAKERS STILL BLOCK BEFORE CONVERSATION")
    # The woman refuses "chess", which is in the man's stated interests, so the
    # pair must be dropped before a conversation is paid for.
    chooser = person("Man A", "male", "early 30s")
    chooser.agent.interests = ["chess", "marathons", "coffee"]
    picky = person("Woman A", "female", "early 30s")
    picky.agent.deal_breakers = ["chess"]

    blocked = Matchmaker(
        llm=CountingLLM(), config=MatchmakingConfig(max_age_gap=40)
    ).run([chooser, picky])
    print(f"  eligible={len(blocked.eligible_matches)} "
          f"conversations={blocked.conversations_held} "
          f"summary={blocked.exclusion_summary}")
    for match in blocked.excluded_pairs:
        for reason in match.rejection_reasons:
            print(f"    excluded: {reason}")
    checks["deal breaker blocks pair"] = not blocked.eligible_matches
    checks["deal breaker counted"] = blocked.exclusion_summary.get("deal_breaker") == 1
    checks["deal breaker costs no conversation"] = blocked.conversations_held == 0

    section("RESULTS")
    for label, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")

    ok = all(checks.values())
    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())