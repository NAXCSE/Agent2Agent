from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Gender(str, Enum):
    """Agent2Agent matches male and female profiles only."""

    MALE = "male"
    FEMALE = "female"


class PersonInput(BaseModel):
    linkedin_url: str
    instagram_url: str


class RawPerson(BaseModel):
    """Raw, unmodified Apify output, kept for inspection."""

    linkedin: List[Dict[str, Any]] = Field(default_factory=list)
    instagram: List[Dict[str, Any]] = Field(default_factory=list)


class AgentPerson(BaseModel):
    """AI-derived profile. This is what an agent knows about its owner."""

    name: str
    gender: Gender

    age_range: Optional[str] = None
    location: Optional[str] = None
    occupation: Optional[str] = None
    headline: Optional[str] = None
    about: Optional[str] = None

    interests: List[str] = Field(default_factory=list)
    values: List[str] = Field(default_factory=list)
    personality_traits: List[str] = Field(default_factory=list)

    looking_for: List[str] = Field(default_factory=list)
    deal_breakers: List[str] = Field(default_factory=list)

    conversation_style: str = "friendly"
    summary: str = ""
    confidence: float = 0.0


class PersonProfile(BaseModel):
    """Scrape-derived person plus the AI profile driving their agent."""

    name: str
    gender: Gender

    linkedin_url: str
    instagram_url: str

    headline: Optional[str] = None
    about: Optional[str] = None
    current_company: Optional[str] = None
    location: Optional[str] = None

    experience: List[Dict[str, Any]] = Field(default_factory=list)
    education: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[str] = Field(default_factory=list)
    certifications: List[Dict[str, Any]] = Field(default_factory=list)

    instagram_bio: Optional[str] = None
    instagram_posts: List[Dict[str, Any]] = Field(default_factory=list)
    instagram_followers: Optional[int] = None

    raw: RawPerson = Field(default_factory=RawPerson)
    agent: Optional[AgentPerson] = None


class DatingAgent(BaseModel):
    """An agent created from a person profile, able to converse as that person."""

    agent_id: str
    persona: AgentPerson
    system_prompt: str


class ConversationTurn(BaseModel):
    speaker_agent_id: str
    message: str


class Conversation(BaseModel):
    agent_a_id: str
    agent_b_id: str
    turns: List[ConversationTurn] = Field(default_factory=list)
    mutual_interest: bool = False
    chemistry_score: float = 0.0
    summary: str = ""
    generated_by: str = "fallback"


class ConversationTurnDraft(BaseModel):
    speaker: str
    message: str


class ConversationDraft(BaseModel):
    """LLM response schema for an agent-to-agent conversation."""

    turns: List[ConversationTurnDraft] = Field(default_factory=list)
    mutual_interest: bool = False
    chemistry_score: float = 0.0
    summary: str = ""


class ConversationVerdict(BaseModel):
    """LLM judgement of one agent-to-agent conversation."""

    interestingness: float = 0.0
    depth: float = 0.0
    chemistry: float = 0.0
    mutual_interest: bool = False
    common_ground: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    verdict: str = ""
    judged_by: str = "fallback"


class MatchmakingConfig(BaseModel):
    """Knobs for controlling cost: shortlist before spending LLM calls."""

    rounds: int = 4
    shortlist_per_person: int = 3
    max_conversations: int = 12
    min_compatibility: float = 0.0
    judge: bool = True
    min_messages: int = 4
    # Free hard gate applied before any conversation is generated.
    # None disables the gate and leaves age as a scored factor only.
    max_age_gap: Optional[float] = 15.0


class CompatibilityFactor(BaseModel):
    name: str
    score: float
    weight: float
    reason: str


class MatchResult(BaseModel):
    person_a_id: str
    person_b_id: str
    person_a_name: str
    person_b_name: str
    gender_pair: str

    eligible: bool = True
    shortlisted: bool = False
    compatibility_score: float = 0.0
    final_score: float = 0.0
    score_breakdown: Dict[str, float] = Field(default_factory=dict)
    factors: List[CompatibilityFactor] = Field(default_factory=list)
    rejection_reasons: List[str] = Field(default_factory=list)
    conversation: Optional[Conversation] = None
    verdict: Optional[ConversationVerdict] = None

    @property
    def message_count(self) -> int:
        return len(self.conversation.turns) if self.conversation else 0


class RankedMatches(BaseModel):
    total_pairs_considered: int = 0
    pairs_shortlisted: int = 0
    conversations_held: int = 0
    eligible_matches: List[MatchResult] = Field(default_factory=list)
    excluded_pairs: List[MatchResult] = Field(default_factory=list)
    settings: Optional[MatchmakingConfig] = None
    # Why pairs were dropped, counted by category, so an empty result is
    # explainable instead of just empty.
    exclusion_summary: Dict[str, int] = Field(default_factory=dict)
    suggestions: List[str] = Field(default_factory=list)

    @property
    def has_matches(self) -> bool:
        return bool(self.eligible_matches)