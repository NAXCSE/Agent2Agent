"""Checks the ApifyService missing/placeholder env-var messages."""

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.apify_service import (  # noqa: E402
    ApifyService,
    ApifyServiceError,
)

REQUIRED = ("APIFY_API_TOKEN", "LINKEDIN_ACTOR_ID", "INSTAGRAM_ACTOR_ID")

# A non-placeholder token so the env checks pass without a real credential.
FAKE_TOKEN = "apify_api_" + "x" * 32


def isolate(**overrides):
    for name in REQUIRED:
        os.environ.pop(name, None)
    os.environ.update({k: v for k, v in overrides.items() if v is not None})


def main() -> int:
    checks = {}

    section = lambda title: print("\n" + title + "\n" + "-" * len(title))

    section("1. all three missing")
    isolate()
    try:
        ApifyService(env_file=Path(r"C:\definitely\not\here\.env"))
        checks["all missing raises"] = False
    except ApifyServiceError as exc:
        message = str(exc)
        print(message)
        checks["all missing raises"] = True
        checks["names every missing var"] = all(name in message for name in REQUIRED)
        checks["mentions Render settings"] = "Environment settings" in message

    section("2. only the two actor ids missing")
    isolate(APIFY_API_TOKEN=FAKE_TOKEN)
    try:
        ApifyService(env_file=Path(r"C:\definitely\not\here\.env"))
        checks["partial missing raises"] = False
    except ApifyServiceError as exc:
        message = str(exc)
        print(message)
        checks["partial missing raises"] = True
        checks["does not blame the token"] = "APIFY_API_TOKEN" not in message
        checks["lists both actor ids"] = (
            "LINKEDIN_ACTOR_ID" in message and "INSTAGRAM_ACTOR_ID" in message
        )

    section("3. one actor id missing")
    isolate(APIFY_API_TOKEN=FAKE_TOKEN, LINKEDIN_ACTOR_ID="owner/actor")
    try:
        ApifyService(env_file=Path(r"C:\definitely\not\here\.env"))
        checks["single missing raises"] = False
    except ApifyServiceError as exc:
        message = str(exc)
        print(message)
        checks["single missing raises"] = True
        checks["names only the missing one"] = (
            "INSTAGRAM_ACTOR_ID" in message and "LINKEDIN_ACTOR_ID" not in message
        )

    section("4. placeholder value")
    isolate(
        APIFY_API_TOKEN="your_apify_token_here",
        LINKEDIN_ACTOR_ID="owner/actor",
        INSTAGRAM_ACTOR_ID="owner/other",
    )
    try:
        ApifyService(env_file=Path(r"C:\definitely\not\here\.env"))
        checks["placeholder raises"] = False
    except ApifyServiceError as exc:
        message = str(exc)
        print(message)
        checks["placeholder raises"] = True
        checks["placeholder not echoed"] = "your_apify_token_here" not in message

    section("5. everything set")
    isolate(
        APIFY_API_TOKEN=FAKE_TOKEN,
        LINKEDIN_ACTOR_ID="owner/actor",
        INSTAGRAM_ACTOR_ID="owner/other",
    )
    service = ApifyService(env_file=Path(r"C:\definitely\not\here\.env"))
    checks["constructs when complete"] = True
    checks["client initialised"] = service.client is not None
    checks["actor ids read"] = (
        service.linkedin_actor_id == "owner/actor"
        and service.instagram_actor_id == "owner/other"
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