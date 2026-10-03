import json
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.llm import available_providers, create_llm
from app.models.schemas import (
    MatchmakingConfig,
    PersonInput,
    PersonProfile,
    RankedMatches,
)
from app.services.apify_service import ApifyService
from app.services.pipeline import Agent2AgentPipeline, PipelineError

router = APIRouter()

DEMO_FILE = Path(__file__).resolve().parents[3] / "data" / "demo_people.json"


class MatchmakingRequest(BaseModel):
    people: List[PersonProfile] = Field(default_factory=list)
    settings: Optional[MatchmakingConfig] = None


class HealthResponse(BaseModel):
    status: str
    llm_providers: List[str]
    llm_model: Optional[str] = None
    llm_ready: bool
    scraping_ready: bool
    config: dict


# Booleans only, never values, so /api/health stays safe to expose.
SCRAPING_VARS = (
    "APIFY_API_TOKEN",
    "LINKEDIN_ACTOR_ID",
    "INSTAGRAM_ACTOR_ID",
)
LLM_VARS = ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _present(name: str) -> bool:
    """Whether an env var is set. Never returns the value itself."""
    return bool((os.getenv(name) or "").strip())


def _pipeline(with_scraper: bool = False) -> Agent2AgentPipeline:
    # No eager LLM client: scraping and analysis need a live one, while ranking
    # degrades to deterministic fallback conversations when none is configured.
    pipeline = Agent2AgentPipeline()
    if with_scraper:
        pipeline.scraper = ApifyService()
    return pipeline


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    model = None
    ready = False
    try:
        client = create_llm()
        model = client.model
        ready = True
    except Exception:  # noqa: BLE001 - health check must not fail hard
        ready = False

    config = {name: _present(name) for name in SCRAPING_VARS + LLM_VARS}

    return HealthResponse(
        status="ok",
        llm_providers=available_providers(),
        llm_model=model,
        llm_ready=ready,
        scraping_ready=all(config[name] for name in SCRAPING_VARS),
        config=config,
    )


@router.get("/demo-people")
def demo_people() -> list:
    """Pre-analyzed demo profiles so the UI works without scraping."""
    if not DEMO_FILE.is_file():
        raise HTTPException(status_code=404, detail="demo_people.json not found")
    return [
        PersonProfile.model_validate(item)
        for item in json.loads(DEMO_FILE.read_text(encoding="utf-8"))
    ]


@router.post("/people/analyze", response_model=PersonProfile)
def analyze_person(request: PersonInput) -> PersonProfile:
    """LinkedIn + Instagram URLs -> scraped data -> AI person profile."""
    try:
        return _pipeline(with_scraper=True).scrape_person(
            request.linkedin_url, request.instagram_url
        )
    except PipelineError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - surfaced as 502
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/matchmaking", response_model=RankedMatches)
def matchmaking(request: MatchmakingRequest) -> RankedMatches:
    """Shortlist -> agents talk -> every conversation judged -> final ranking."""
    if len(request.people) < 2:
        raise HTTPException(
            status_code=400,
            detail="At least two analyzed people are required to rank matches.",
        )

    try:
        return _pipeline().rank_people(
            request.people,
            include_conversations=True,
            config=request.settings,
        )
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - surfaced as 502
        raise HTTPException(status_code=502, detail=str(exc)) from None


@router.post("/matches", response_model=RankedMatches)
def rank_matches(request: MatchmakingRequest) -> RankedMatches:
    """Default-settings alias of /matchmaking."""
    request.settings = request.settings or MatchmakingConfig()
    return matchmaking(request)