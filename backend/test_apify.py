import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Windows consoles default to cp1252, which cannot print raw scraped text.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.services.apify_service import ApifyService, ApifyServiceError  # noqa: E402

LINKEDIN_URL = "https://www.linkedin.com/in/williamhgates"
INSTAGRAM_URL = "https://www.instagram.com/billgates"


def _section(title: str, payload) -> None:
    print()
    print("==============================")
    print(title)
    print("==============================")
    print()
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def main() -> int:
    if "PASTE" in LINKEDIN_URL or "PASTE" in INSTAGRAM_URL:
        print(
            "Set LINKEDIN_URL and INSTAGRAM_URL at the top of this file "
            "before running the test."
        )
        return 1

    scraper = ApifyService()

    result = scraper.scrape_person(LINKEDIN_URL, INSTAGRAM_URL)

    _section("LINKEDIN RESULT", result["linkedin"])
    _section("INSTAGRAM RESULT", result["instagram"])

    linkedin_items = result["linkedin"]
    instagram_items = result["instagram"]

    print()
    print("==============================")
    print("SUMMARY")
    print("==============================")
    print()
    print(f"LinkedIn items : {len(linkedin_items)}")
    print(f"Instagram items: {len(instagram_items)}")

    ok = bool(linkedin_items) and bool(instagram_items)
    print()
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApifyServiceError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)