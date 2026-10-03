"""Checks that /api/health reports config presence without leaking values."""

import json
import os
import sys
import warnings
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SCRAPING_VARS = ("APIFY_API_TOKEN", "LINKEDIN_ACTOR_ID", "INSTAGRAM_ACTOR_ID")

FAKE = {
    "APIFY_API_TOKEN": "apify_api_" + "a" * 32,
    "LINKEDIN_ACTOR_ID": "owner/linkedin-actor",
    "INSTAGRAM_ACTOR_ID": "owner/instagram-actor",
    "GEMINI_API_KEY": "AIzaFAKEKEY" + "b" * 30,
}

MANAGED = tuple(FAKE) + ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def call_health(env):
    """Set env vars to the given values; an empty string means 'not set'.

    Empty string rather than deleting, because load_dotenv() runs during the
    request and would refill anything missing from the local .env file.
    """
    saved = {name: os.environ.get(name) for name in MANAGED}
    try:
        for name in MANAGED:
            os.environ[name] = env.get(name, "")
        response = TestClient(app).get("/api/health")
        return response.status_code, response.json()
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main() -> int:
    checks = {}

    print("1. everything configured")
    status, body = call_health(FAKE)
    print(json.dumps(body, indent=2))
    checks["health returns 200"] = status == 200
    checks["scraping_ready true when complete"] = body["scraping_ready"] is True
    checks["reports every scraping var"] = all(
        name in body["config"] for name in SCRAPING_VARS
    )

    print()
    print("2. actor ids missing")
    partial = dict(FAKE)
    partial["LINKEDIN_ACTOR_ID"] = ""
    partial["INSTAGRAM_ACTOR_ID"] = ""
    _, body = call_health(partial)
    print(json.dumps(body["config"], indent=2))
    checks["scraping_ready false"] = body["scraping_ready"] is False
    checks["flags missing actor ids"] = (
        body["config"]["LINKEDIN_ACTOR_ID"] is False
        and body["config"]["INSTAGRAM_ACTOR_ID"] is False
    )
    checks["still flags the present token"] = body["config"]["APIFY_API_TOKEN"] is True

    print()
    print("3. token missing too")
    nothing = {name: "" for name in FAKE}
    _, body = call_health(nothing)
    print(json.dumps(body["config"], indent=2))
    checks["scraping_ready false with nothing set"] = body["scraping_ready"] is False
    checks["llm_ready false with no key"] = body["llm_ready"] is False

    print()
    print("4. no secrets in the payload")
    _, body = call_health(FAKE)
    raw = json.dumps(body)
    leaked = [name for name, value in FAKE.items() if value in raw]
    print("leaked:", leaked or "none")
    checks["no env values in response"] = not leaked
    checks["all config values are bools"] = all(
        isinstance(value, bool) for value in body["config"].values()
    )

    print()
    for label, passed in checks.items():
        print(f"[{'PASS' if passed else 'FAIL'}] {label}")
    ok = all(checks.values())
    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())