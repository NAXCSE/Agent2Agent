import re
from itertools import combinations
from typing import Dict, List, Optional, Tuple

from app.models.schemas import (
    AgentPerson,
    CompatibilityFactor,
    Conversation,
    DatingAgent,
    Gender,
    MatchResult,
    PersonProfile,
    RankedMatches,
)
from app.services.dating_engine import DatingEngine

WEIGHTS: Dict[str, float] = {
    "shared_interests": 0.30,
    "looking_for": 0.18,
    "personality": 0.15,
    "location": 0.15,
    "shared_values": 0.12,
    "age": 0.10,
}

STOP_WORDS = {"and", "the", "of", "a", "an", "to", "in", "for", "with", "on"}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", str(value).lower())


def _tokens(values: List[str]) -> set:
    out = set()
    for value in values:
        for token in _norm(value).split():
            if len(token) > 2 and token not in STOP_WORDS:
                out.add(token)
    return out


def _jaccard(left: set, right: set) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


_AGE_BAND = re.compile(r"(early|mid|late|mid-early|mid-late)?\s*(\d{2})\s*s?\b", re.IGNORECASE)

_QUALIFIER_OFFSET = {
    "early": 2,
    "mid-early": 4,
    "mid": 5,
    "mid-late": 7,
    "late": 8,
}


def age_midpoint(value: Optional[str]) -> Optional[float]:
    """Best guess at an age from a phrase such as "late 60s", "29s" or "31".

    "late 60s" -> 68, "mid 30s" -> 35, "29s" -> 29, "20s" -> 24.5.
    Returns None when the age cannot be read, so callers can treat it as
    unknown instead of guessing.
    """
    if not value:
        return None

    match = _AGE_BAND.search(str(value))
    if not match:
        return None

    qualifier = (match.group(1) or "").lower()
    base = int(match.group(2))

    if qualifier:
        return base + _QUALIFIER_OFFSET.get(qualifier, 5)

    # Bare decade such as "20s" spans ten years; a bare age such as "29s" is exact.
    if re.search(r"20s|10s|30s", str(value).strip(), re.IGNORECASE) and base % 10 == 0:
        return base + 4.5

    return float(base)


def _location_score(a: AgentPerson, b: AgentPerson) -> Tuple[float, str]:
    loc_a, loc_b = _norm(a.location or ""), _norm(b.location or "")
    if not loc_a or not loc_b:
        return 0.5, "Location unknown for at least one profile, treated as neutral."

    if loc_a == loc_b:
        return 1.0, f"Both are based in {a.location}."

    country_a = loc_a.split()[-1]
    country_b = loc_b.split()[-1]
    if country_a and country_a == country_b:
        return 0.7, f"Same country ({a.location} vs {b.location}), different cities."

    return 0.3, f"Different locations: {a.location} vs {b.location}."


def _age_score(a: AgentPerson, b: AgentPerson) -> Tuple[float, str]:
    age_a, age_b = age_midpoint(a.age_range), age_midpoint(b.age_range)
    if age_a is None or age_b is None:
        return 0.5, "Age unknown for at least one profile, treated as neutral."

    gap = abs(age_a - age_b)
    if gap <= 2:
        return 1.0, f"Very close age ({a.age_range} vs {b.age_range})."
    if gap <= 5:
        return 0.8, f"Comparable age ({a.age_range} vs {b.age_range})."
    if gap <= 10:
        return 0.5, f"Noticeable but workable age gap of ~{gap:.0f} years."
    return 0.2, f"Large age gap of ~{gap:.0f} years ({a.age_range} vs {b.age_range})."


def age_gap(a: AgentPerson, b: AgentPerson) -> Optional[float]:
    age_a, age_b = age_midpoint(a.age_range), age_midpoint(b.age_range)
    if age_a is None or age_b is None:
        return None
    return abs(age_a - age_b)


def _deal_breakers(a: AgentPerson, b: AgentPerson) -> List[str]:
    """Hard filters: anything either side explicitly refuses."""
    reasons: List[str] = []

    def described_by(person: AgentPerson) -> str:
        return _norm(
            " ".join(
                [
                    person.occupation or "",
                    " ".join(person.personality_traits),
                    " ".join(person.interests),
                ]
            )
        )

    for owner, other in ((a, b), (b, a)):
        other_text = f" {described_by(other)} "
        for breaker in owner.deal_breakers:
            needle = f" {_norm(breaker).strip()} "
            if len(needle.strip()) > 2 and needle in other_text:
                reasons.append(
                    f"{owner.name} lists '{breaker}' as a deal breaker, "
                    f"which matches {other.name}'s profile."
                )

    return reasons


class RankingEngine:
    """Scores, filters and ranks male/female pairs. No LLM required.

    max_age_gap is a hard gate: an unrealistic age gap drops the pair before it
    can cost a conversation, instead of only denting its score.
    """

    def __init__(
        self,
        dating_engine: Optional[DatingEngine] = None,
        include_conversations: bool = True,
        conversation_limit: int = 5,
        max_age_gap: Optional[float] = None,
    ) -> None:
        self.dating_engine = dating_engine or DatingEngine()
        self.include_conversations = include_conversations
        self.conversation_limit = conversation_limit
        self.max_age_gap = max_age_gap

    def score_pair(self, a: AgentPerson, b: AgentPerson) -> List[CompatibilityFactor]:
        interests = _jaccard(_tokens(a.interests), _tokens(b.interests))
        values = _jaccard(_tokens(a.values), _tokens(b.values))
        traits = _jaccard(_tokens(a.personality_traits), _tokens(b.personality_traits))

        a_wants = _tokens(a.looking_for)
        b_wants = _tokens(b.looking_for)
        a_is = _tokens(a.personality_traits) | _tokens(a.interests)
        b_is = _tokens(b.personality_traits) | _tokens(b.interests)
        looking_for = (_jaccard(a_wants, b_is) + _jaccard(b_wants, a_is)) / 2

        location_score, location_reason = _location_score(a, b)
        age_score, age_reason = _age_score(a, b)

        return [
            CompatibilityFactor(
                name="shared_interests",
                score=round(interests, 3),
                weight=WEIGHTS["shared_interests"],
                reason=(
                    "Shared interests detected."
                    if interests > 0
                    else "No overlapping interests found."
                ),
            ),
            CompatibilityFactor(
                name="looking_for",
                score=round(looking_for, 3),
                weight=WEIGHTS["looking_for"],
                reason="How well each side matches what the other is looking for.",
            ),
            CompatibilityFactor(
                name="personality",
                score=round(traits, 3),
                weight=WEIGHTS["personality"],
                reason="Overlap of stated personality traits.",
            ),
            CompatibilityFactor(
                name="location",
                score=round(location_score, 3),
                weight=WEIGHTS["location"],
                reason=location_reason,
            ),
            CompatibilityFactor(
                name="shared_values",
                score=round(values, 3),
                weight=WEIGHTS["shared_values"],
                reason=(
                    "Shared values detected."
                    if values > 0
                    else "No explicit overlap in stated values."
                ),
            ),
            CompatibilityFactor(
                name="age",
                score=round(age_score, 3),
                weight=WEIGHTS["age"],
                reason=age_reason,
            ),
        ]

    @staticmethod
    def _compatibility_score(factors: List[CompatibilityFactor]) -> float:
        total_weight = sum(f.weight for f in factors)
        if not total_weight:
            return 0.0
        weighted = sum(f.score * f.weight for f in factors)
        return round(100 * weighted / total_weight, 2)

    @staticmethod
    def _order_genders(
        first: PersonProfile,
        second: PersonProfile,
    ) -> Tuple[PersonProfile, PersonProfile]:
        """Male is always person A so the gender pair reads male -> female."""
        if first.gender == Gender.MALE:
            return first, second
        return second, first

    def rank(
        self,
        people: List[PersonProfile],
        agents: Optional[Dict[str, DatingAgent]] = None,
    ) -> RankedMatches:
        eligible: List[MatchResult] = []
        excluded: List[MatchResult] = []
        considered = 0

        agents = agents or {}
        agent_ids = {
            agent.persona.name: agent_id for agent_id, agent in agents.items()
        }

        for first, second in combinations(people, 2):
            considered += 1
            person_a, person_b = self._order_genders(first, second)
            a = person_a.agent or AgentPerson(
                name=person_a.name, gender=person_a.gender
            )
            b = person_b.agent or AgentPerson(
                name=person_b.name, gender=person_b.gender
            )
            gender_pair = f"{a.gender.value} -> {b.gender.value}"

            id_a = agent_ids.get(person_a.name, person_a.name)
            id_b = agent_ids.get(person_b.name, person_b.name)

            if a.gender == b.gender:
                excluded.append(
                    MatchResult(
                        person_a_id=id_a,
                        person_b_id=id_b,
                        person_a_name=person_a.name,
                        person_b_name=person_b.name,
                        gender_pair=gender_pair,
                        eligible=False,
                        rejection_reasons=[
                            f"Same gender pairing is not supported: both are "
                            f"{a.gender.value}. Agent2Agent matches male and female only."
                        ],
                    )
                )
                continue

            rejection_reasons = _deal_breakers(a, b)

            if self.max_age_gap is not None:
                gap = age_gap(a, b)
                if gap is not None and gap > self.max_age_gap:
                    rejection_reasons.append(
                        f"Age gap of ~{gap:.0f} years is above the configured "
                        f"limit of {self.max_age_gap:.0f} "
                        f"({a.age_range} vs {b.age_range})."
                    )

            if rejection_reasons:
                excluded.append(
                    MatchResult(
                        person_a_id=id_a,
                        person_b_id=id_b,
                        person_a_name=person_a.name,
                        person_b_name=person_b.name,
                        gender_pair=gender_pair,
                        eligible=False,
                        rejection_reasons=rejection_reasons,
                    )
                )
                continue

            factors = self.score_pair(a, b)
            eligible.append(
                MatchResult(
                    person_a_id=id_a,
                    person_b_id=id_b,
                    person_a_name=person_a.name,
                    person_b_name=person_b.name,
                    gender_pair=gender_pair,
                    eligible=True,
                    compatibility_score=self._compatibility_score(factors),
                    factors=factors,
                )
            )

        eligible.sort(key=lambda m: m.compatibility_score, reverse=True)

        if self.include_conversations and agents:
            self._attach_conversations(eligible, agents)
            eligible.sort(key=lambda m: m.compatibility_score, reverse=True)

        return RankedMatches(
            total_pairs_considered=considered,
            eligible_matches=eligible,
            excluded_pairs=excluded,
        )

    def _attach_conversations(
        self,
        matches: List[MatchResult],
        agents: Dict[str, DatingAgent],
    ) -> None:
        for match in matches[: self.conversation_limit]:
            agent_a = agents.get(match.person_a_id)
            agent_b = agents.get(match.person_b_id)
            if not agent_a or not agent_b:
                continue
            conversation: Optional[Conversation] = self.dating_engine.date(
                agent_a, agent_b
            )
            match.conversation = conversation
            if conversation.generated_by == "llm":
                match.compatibility_score = round(
                    0.7 * match.compatibility_score + 0.3 * conversation.chemistry_score * 100,
                    2,
                )