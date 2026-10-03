from typing import List, Optional

from app.llm import LLMClient
from app.models.schemas import (
    AgentPerson,
    Gender,
    MatchmakingConfig,
    PersonInput,
    PersonProfile,
    RankedMatches,
)
from app.services.apify_service import ApifyService
from app.services.profile_analyzer import ProfileAnalyzer
from app.services.matchmaking import Matchmaker


class PipelineError(RuntimeError):
    """Raised when the end-to-end pipeline cannot complete."""


def _first(items):
    return items[0] if items else {}


def build_person_profile(
    raw: dict,
    linkedin_url: str,
    instagram_url: str,
    agent: Optional[AgentPerson] = None,
) -> PersonProfile:
    """Flatten raw Apify output into a PersonProfile.

    The untouched payload always stays on `.raw`, so nothing is lost.
    """
    linkedin = raw.get("linkedin") or []
    instagram = raw.get("instagram") or []
    li = _first(linkedin)
    ig = _first(instagram)

    experience = li.get("experience") or []
    current_position = li.get("currentPosition") or []
    current_company = None
    if current_position and isinstance(current_position[0], dict):
        current_company = current_position[0].get("companyName")

    location = None
    li_location = li.get("location")
    if isinstance(li_location, dict):
        location = li_location.get("linkedinText") or (
            li_location.get("parsed") or {}
        ).get("text")

    name = " ".join(
        part for part in (li.get("firstName"), li.get("lastName")) if part
    ).strip()
    if not name:
        name = ig.get("fullName") or ig.get("username") or "Unknown"

    gender: Gender = (agent.gender if agent else Gender.FEMALE)

    return PersonProfile(
        name=name,
        gender=gender,
        linkedin_url=linkedin_url,
        instagram_url=instagram_url,
        headline=li.get("headline"),
        about=li.get("about"),
        current_company=current_company,
        location=location or (agent.location if agent else None),
        experience=experience,
        education=li.get("education") or [],
        skills=[
            s.get("name") if isinstance(s, dict) else s
            for s in (li.get("skills") or [])
        ],
        certifications=li.get("certifications") or [],
        instagram_bio=ig.get("biography"),
        instagram_posts=ig.get("latestPosts") or [],
        instagram_followers=ig.get("followersCount"),
        raw=raw,
        agent=agent,
    )


class Agent2AgentPipeline:
    """Scraped profiles -> AI agents -> conversations -> final ranking."""

    def __init__(
        self,
        scraper: Optional[ApifyService] = None,
        analyzer: Optional[ProfileAnalyzer] = None,
        llm: Optional[LLMClient] = None,
    ) -> None:
        self.scraper = scraper
        self._llm = llm
        self.analyzer = analyzer or ProfileAnalyzer(llm=llm)

    def scrape_person(self, linkedin_url: str, instagram_url: str) -> PersonProfile:
        """Stages 1-3: Apify -> AI analysis -> PersonProfile."""
        if self.scraper is None:
            raise PipelineError("No Apify scraper configured for scraping.")

        raw = self.scraper.scrape_person(linkedin_url, instagram_url)
        agent = self.analyzer.analyze(raw["linkedin"], raw["instagram"])
        return build_person_profile(raw, linkedin_url, instagram_url, agent=agent)

    def rank_people(
        self,
        people: List[PersonProfile],
        include_conversations: bool = True,
        conversation_limit: Optional[int] = None,
        config: Optional[MatchmakingConfig] = None,
    ) -> RankedMatches:
        """Stages 4-6: agents date each other, get judged, then ranked."""
        if any(person.agent is None for person in people):
            raise PipelineError(
                "Every person needs an AI profile before ranking. "
                "Run the analysis stage first."
            )

        config = config or MatchmakingConfig()
        config = config.model_copy(
            update={
                "judge": config.judge and include_conversations,
                "max_conversations": (
                    conversation_limit
                    if conversation_limit is not None
                    else config.max_conversations
                ),
            }
        )

        return Matchmaker(llm=self._llm, config=config).run(people)

    def run(
        self,
        inputs: List[PersonInput],
        include_conversations: bool = True,
    ) -> tuple[List[PersonProfile], RankedMatches]:
        people = [
            self.scrape_person(item.linkedin_url, item.instagram_url)
            for item in inputs
        ]
        return people, self.rank_people(
            people,
            include_conversations=include_conversations,
        )