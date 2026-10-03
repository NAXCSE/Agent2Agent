from typing import Dict, List, Optional, Tuple

from app.llm import LLMClient
from app.models.schemas import (
    DatingAgent,
    MatchResult,
    MatchmakingConfig,
    PersonProfile,
    RankedMatches,
)
from app.prompts.profile_prompt import SCORE_WEIGHTS
from app.services.dating_engine import DatingEngine
from app.services.judge import ConversationJudge
from app.services.ranking_engine import RankingEngine

SHORT_MESSAGE_PENALTY = 0.8
COMMON_GROUND_TARGET = 3


def categorize(reason: str) -> str:
    lowered = reason.lower()
    if "same gender" in lowered:
        return "same_gender"
    if "deal breaker" in lowered:
        return "deal_breaker"
    if "age gap" in lowered:
        return "age_gap"
    return "other"


def summarize_exclusions(excluded: List[MatchResult]) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for match in excluded:
        for reason in match.rejection_reasons:
            key = categorize(reason)
            summary[key] = summary.get(key, 0) + 1
    return summary


def build_suggestions(
    summary: Dict[str, int],
    config: MatchmakingConfig,
    total_pairs: int,
    shortlisted: int,
) -> List[str]:
    """Actionable hints when the result list is empty or thin."""
    if not summary and shortlisted:
        return []

    suggestions: List[str] = []

    if summary.get("age_gap", 0) > 0:
        limit = (
            f"{config.max_age_gap:.0f} years"
            if config.max_age_gap is not None
            else "the configured limit"
        )
        suggestions.append(
            f"{summary['age_gap']} pair(s) were dropped by the age gate "
            f"(limit {limit}). Increase max_age_gap in the settings to allow "
            f"wider gaps."
        )
    if summary.get("deal_breaker", 0) > 0:
        suggestions.append(
            f"{summary['deal_breaker']} pair(s) crossed a stated deal breaker. "
            f"Review the deal breakers in the AI profiles if that is too strict."
        )
    if not summary and total_pairs and not shortlisted:
        suggestions.append(
            "Every pair scored below min_compatibility. Lower the minimum or add "
            "more people to the pool."
        )
    if not suggestions and not summary:
        suggestions.append("Add more people to the pool to get matches.")

    return suggestions


class Matchmaker:
    """Shortlist -> converse -> judge -> rank.

    Running every pair is expensive (12 male x 12 female = 144 conversations),
    so compatibility is scored for free first and only the most promising
    pairs are allowed to spend LLM calls.
    """

    def __init__(
        self,
        llm: Optional[LLMClient] = None,
        config: Optional[MatchmakingConfig] = None,
    ) -> None:
        self.config = config or MatchmakingConfig()
        self.dating_engine = DatingEngine(llm=llm, rounds=self.config.rounds)
        self.judge = ConversationJudge(llm=llm)
        self.ranking = RankingEngine(
            include_conversations=False,
            max_age_gap=self.config.max_age_gap,
        )

    def shortlist(
        self,
        matches: List[MatchResult],
        shortlist_per_person: int,
        min_compatibility: float,
    ) -> Tuple[List[MatchResult], List[MatchResult]]:
        """Keep the best few partners per person, plus anything above the floor."""
        candidates = [
            m for m in matches if m.eligible and m.compatibility_score >= min_compatibility
        ]

        per_person: Dict[str, int] = {}
        kept: List[MatchResult] = []

        for match in sorted(candidates, key=lambda m: m.compatibility_score, reverse=True):
            counts = (
                per_person.get(match.person_a_id, 0),
                per_person.get(match.person_b_id, 0),
            )
            if counts[0] >= shortlist_per_person and counts[1] >= shortlist_per_person:
                continue
            per_person[match.person_a_id] = counts[0] + 1
            per_person[match.person_b_id] = counts[1] + 1
            match.shortlisted = True
            kept.append(match)

        kept.sort(key=lambda m: m.compatibility_score, reverse=True)
        return kept, [m for m in candidates if m not in kept]

    @staticmethod
    def _common_ground_score(count: int) -> float:
        return min(1.0, count / COMMON_GROUND_TARGET)

    def _final_score(self, match: MatchResult) -> Tuple[float, Dict[str, float]]:
        verdict = match.verdict
        breakdown: Dict[str, float] = {
            "compatibility": match.compatibility_score,
        }
        if verdict is None:
            return round(match.compatibility_score, 2), breakdown

        breakdown.update(
            {
                "interestingness": round(verdict.interestingness * 100, 2),
                "depth": round(verdict.depth * 100, 2),
                "common_ground": round(
                    self._common_ground_score(len(verdict.common_ground)) * 100, 2
                ),
                "mutual_interest": 100.0 if verdict.mutual_interest else 0.0,
            }
        )

        total = sum(
            weight * breakdown.get(name, 0.0) for name, weight in SCORE_WEIGHTS.items()
        )

        if match.message_count < self.config.min_messages:
            total *= SHORT_MESSAGE_PENALTY
            breakdown["short_conversation_penalty"] = round(
                100 * (SHORT_MESSAGE_PENALTY - 1), 2
            )

        return round(total, 2), breakdown

    def run(self, people: List[PersonProfile]) -> RankedMatches:
        if len(people) < 2:
            raise ValueError("At least two analyzed people are required.")

        agents = {
            agent.agent_id: agent
            for agent in (
                self.dating_engine.create_agent(person.agent)
                for person in people
                if person.agent is not None
            )
        }

        base = self.ranking.rank(people, agents=agents)

        shortlisted, not_shortlisted = self.shortlist(
            base.eligible_matches,
            self.config.shortlist_per_person,
            self.config.min_compatibility,
        )

        conversing = shortlisted[: self.config.max_conversations]
        conversed: List[MatchResult] = []

        for match in conversing:
            agent_a = agents.get(match.person_a_id)
            agent_b = agents.get(match.person_b_id)
            if not agent_a or not agent_b:
                continue

            conversation = self.dating_engine.date(agent_a, agent_b)
            match.conversation = conversation
            conversed.append(match)

            if self.config.judge:
                match.verdict = self.judge.judge(agent_a, agent_b, conversation)

        for match in shortlisted:
            final, breakdown = self._final_score(match)
            match.final_score = final
            match.score_breakdown = breakdown

        conversed_ids = {id(match) for match in conversed}
        overflow = [m for m in conversing if id(m) not in conversed_ids]

        for match in overflow + not_shortlisted:
            final, breakdown = self._final_score(match)
            match.final_score = final
            match.score_breakdown = breakdown

        everything = sorted(
            shortlisted + not_shortlisted + overflow,
            key=lambda m: m.final_score,
            reverse=True,
        )

        summary = summarize_exclusions(base.excluded_pairs)

        return RankedMatches(
            total_pairs_considered=base.total_pairs_considered,
            pairs_shortlisted=len(shortlisted),
            conversations_held=len(conversed),
            eligible_matches=everything,
            excluded_pairs=base.excluded_pairs,
            settings=self.config,
            exclusion_summary=summary,
            suggestions=build_suggestions(
                summary, self.config, base.total_pairs_considered, len(shortlisted)
            ),
        )