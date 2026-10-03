import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

FRONTEND_DIR = BACKEND_DIR.parent / "frontend"

app = FastAPI(
    title="Agent2Agent",
    description=(
        "Scrape LinkedIn and Instagram profiles, turn them into AI dating "
        "agents, let the agents talk, judge every conversation and rank "
        "male/female matches."
    ),
    version="0.2.0",
)

# The Vercel-hosted UI calls this API cross-origin.
cors_origins = [
    origin.strip()
    for origin in (os.getenv("CORS_ORIGINS") or "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials="*" not in cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/api", tags=["meta"])
def api_info() -> dict:
    return {
        "service": "Agent2Agent",
        "stage": "scraping -> analysis -> agents -> conversations -> judgement -> ranking",
        "ui": "/",
        "docs": "/docs",
        "endpoints": [
            "GET  /api/health",
            "GET  /api/demo-people",
            "POST /api/people/analyze",
            "POST /api/matchmaking",
            "POST /api/matches",
        ],
    }


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
    # Mounted last so it serves the UI at "/" without shadowing /api.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")