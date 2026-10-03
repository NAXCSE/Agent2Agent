"""Offline end-to-end test of the Agent2Agent pipeline.

Runs the stages that need no credentials:

    AI profiles -> agents -> agent-to-agent conversations -> ranking

Scraping and live LLM analysis are covered by test_apify.py and
test_matchmaking.py.
"""

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(PROJECT_DIR))

from app.models.schemas import PersonProfile  # noqa: E402
from app.services.dating_engine import DatingEngine, DatingEngineError  # noqa: E402
from app.services.pipeline import Agent2AgentPipeline  # noqa: E402
from app.services.ranking_engine import RankingEngine  # noqa: E402

DEMO_FILE = PROJECT_DIR / "data" / "demo_people.json"
CONVERSATION_LIMIT = 2


def load_people():
    payload = json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    return [PersonProfile.model_validate(item) for item in payload]


def section(title: str) -> None:
    print()
    print("==============================")
    print(title)
    print("==============================")
    print()


def main() -> int:
    people = load_people()
    males = [p for p in people if p.gender.value == "male"]
    females = [p for p in people if p.gender.value == "female"]
    expected_pairs = len(males) * len(females)
    total_pairs = len(people) * (len(people) - 1) // 2

    section("INPUT PEOPLE")
    print(f"total={len(people)} male={len(males)} female={len(females)}")
    for person in people[:6]:
        agent = person.agent
        print(
            f"- {person.name:<18} {person.gender.value:<6} "
            f"{agent.occupation if agent else '?'}"
        )
    if len(people) > 6:
        print(f"... and {len(people) - 6} more")

    section("AGENT CREATION")
    engine = DatingEngine()
    agents = {
        agent.agent_id: agent
        for agent in (engine.create_agent(p.agent) for p in people)
    }
    for agent_id, agent in list(agents.items())[:4]:
        prompt_lines = [
            line for line in agent.system_prompt.splitlines() if line.strip()
        ]
        print(f"{agent_id}  ({len(prompt_lines)} prompt lines, "
              f"style={agent.persona.conversation_style})")
    print(f"agents created: {len(agents)}")

    section("SAME-GENDER GUARD (male/female only)")
    male_ids = [aid for aid in agents if aid.startswith("male-")]
    try:
        engine.date(agents[male_ids[0]], agents[male_ids[1]])
        print("FAIL: same-gender pair was not rejected")
        return 1
    except DatingEngineError as exc:
        print(f"OK: {exc}")

    section("RANKING (deterministic compatibility, free)")
    ranking = RankingEngine(include_conversations=False)
    result = ranking.rank(people)

    for match in result.eligible_matches[:5]:
        print(f"{match.person_a_name} <-> {match.person_b_name} "
              f"[{match.gender_pair}] score={match.compatibility_score}")
        for factor in match.factors:
            print(f"    - {factor.name:<17} {factor.score:.3f} "
                  f"x{factor.weight:.2f}  {factor.reason}")

    section("EXCLUDED PAIRS")
    print(f"total excluded: {len(result.excluded_pairs)}")
    for match in result.excluded_pairs[:4]:
        for reason in match.rejection_reasons:
            print(f"{match.person_a_name} + {match.person_b_name}: {reason}")

    section("AGENTS DATE EACH OTHER")
    pipeline = Agent2AgentPipeline()
    final = pipeline.rank_people(
        people,
        include_conversations=True,
        conversation_limit=CONVERSATION_LIMIT,
    )

    for match in final.eligible_matches[:CONVERSATION_LIMIT]:
        conversation = match.conversation
        print()
        print(f"--- {match.person_a_name} ({match.gender_pair}) "
              f"{match.person_b_name} ---")
        if conversation is None:
            print("no conversation generated")
            continue
        print(f"generated_by={conversation.generated_by} "
              f"chemistry={conversation.chemistry_score} "
              f"mutual_interest={conversation.mutual_interest}")
        for turn in conversation.turns:
            speaker = (
                match.person_a_name
                if turn.speaker_agent_id == match.person_a_id
                else match.person_b_name
            )
            print(f"  {speaker}: {turn.message}")
        print(f"  summary: {conversation.summary}")
        print(f"  final score: {match.final_score}")

    section("FINAL RANKING (top 10)")
    for index, match in enumerate(final.eligible_matches[:10], start=1):
        print(f"{index:>2}. {match.person_a_name} + {match.person_b_name} "
              f"-> {match.final_score} ({match.gender_pair})")

    section("CHECKS")
    names_male = {p.name for p in males}
    names_female = {p.name for p in females}
    same_gender_ranked = [
        m for m in final.eligible_matches
        if (m.person_a_name in names_male and m.person_b_name in names_male)
        or (m.person_a_name in names_female and m.person_b_name in names_female)
    ]

    checks = {
        "gender enum is male/female only": {
            p.gender.value for p in people
        } <= {"male", "female"},
        "one agent per person": len(agents) == len(people),
        "all pairs considered": result.total_pairs_considered == total_pairs,
        "only male/female pairs scored": len(result.eligible_matches) == expected_pairs,
        "same-gender pairs excluded": len(result.excluded_pairs) == total_pairs - expected_pairs,
        "no same-gender pair survives": not same_gender_ranked,
        "ranking is descending": all(
            final.eligible_matches[i].final_score
            >= final.eligible_matches[i + 1].final_score
            for i in range(len(final.eligible_matches) - 1)
        ),
        "conversations generated for top matches": all(
            match.conversation is not None
            for match in final.eligible_matches[:CONVERSATION_LIMIT]
        ),
        "conversations only for top matches": all(
            match.conversation is None
            for match in final.eligible_matches[CONVERSATION_LIMIT:]
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