import os
import re
import warnings
from pathlib import Path
from typing import Any, Dict, List

from apify_client import ApifyClient
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"

REQUIRED_VARS = (
    "APIFY_API_TOKEN",
    "LINKEDIN_ACTOR_ID",
    "INSTAGRAM_ACTOR_ID",
)

_PLACEHOLDER_PATTERN = re.compile(
    r"^(?:your_|xxx|xxx_|paste_|changeme|<|todo|replace)", re.IGNORECASE
)
_TOKEN_LIKE_PATTERN = re.compile(r"apify_api_[A-Za-z0-9]{10,}")


class ApifyServiceError(RuntimeError):
    """Raised when the Apify scraping layer fails."""


class ApifyService:
    """Thin wrapper around the Apify Actors used by Agent2Agent."""

    def __init__(self, env_file: Path = ENV_FILE) -> None:
        self._load_env(env_file)

        missing: List[str] = []
        for name in REQUIRED_VARS:
            if not self._read(name):
                missing.append(name)
        if missing:
            raise ApifyServiceError(self._missing_env_message(missing))

        self.api_token = self._require("APIFY_API_TOKEN")
        self.linkedin_actor_id = self._require("LINKEDIN_ACTOR_ID")
        self.instagram_actor_id = self._require("INSTAGRAM_ACTOR_ID")

        self.client = ApifyClient(self.api_token)

    @staticmethod
    def _missing_env_message(missing: List[str]) -> str:
        listed = ", ".join(missing)
        plural = "s" if len(missing) > 1 else ""
        return (
            f"Missing required environment variable{plural}: {listed}. "
            f"Locally, add {listed} to {ENV_FILE}. "
            f"When deployed (Render), set {listed} under the service's "
            f"Environment settings, not in a .env file. "
            f"See backend/.env.example for the expected values."
        )

    @staticmethod
    def _load_env(env_file: Path) -> None:
        if env_file.is_file():
            load_dotenv(env_file)

    @staticmethod
    def _read(name: str) -> str:
        return (os.getenv(name) or "").strip()

    def _require(self, name: str) -> str:
        value = self._read(name)

        if not value:
            raise ApifyServiceError(
                f"Missing required environment variable: {name}. "
                f"Locally, add it to {ENV_FILE}. When deployed (Render), set it "
                f"under the service's Environment settings. "
                f"See backend/.env.example."
            )

        if _PLACEHOLDER_PATTERN.match(value):
            raise ApifyServiceError(
                f"Environment variable {name} still holds a placeholder value. "
                f"Set a real value in {ENV_FILE} (locally) or in the host's "
                f"environment settings (deployed)."
            )

        return value

    def _redact(self, text: str) -> str:
        cleaned = str(text).replace(self.api_token, "***")
        return _TOKEN_LIKE_PATTERN.sub("***", cleaned)

    @staticmethod
    def _normalize_url(url: str, source: str) -> str:
        if not url or not url.strip():
            raise ApifyServiceError(
                f"{source} scraping failed: no URL provided."
            )

        cleaned = url.strip()
        if not cleaned.lower().startswith(("http://", "https://")):
            cleaned = f"https://{cleaned}"

        return cleaned.rstrip("/")

    def _run_actor(
        self,
        actor_id: str,
        run_input: Dict[str, Any],
        source: str,
    ) -> List[Dict[str, Any]]:
        try:
            run = self.client.actor(actor_id).call(run_input=run_input)
        except Exception as exc:  # noqa: BLE001 - surfaced with context below
            raise ApifyServiceError(
                f"{source} scraping failed: {self._redact(exc)}"
            ) from None

        run = dict(run or {})
        dataset_id = run.get("defaultDatasetId") or run.get("default_dataset_id")
        status = run.get("status")

        if status and status != "SUCCEEDED":
            detail = (
                run.get("errorMessage")
                or run.get("error_message")
                or f"actor run finished with status '{status}'"
            )
            raise ApifyServiceError(
                f"{source} scraping failed: {self._redact(detail)}"
            )

        if not dataset_id:
            raise ApifyServiceError(
                f"{source} scraping failed: actor run produced no dataset."
            )

        try:
            items = list(
                self.client.dataset(dataset_id).iterate_items()
            )
        except Exception as exc:  # noqa: BLE001 - surfaced with context below
            raise ApifyServiceError(
                f"{source} scraping failed while reading dataset: "
                f"{self._redact(exc)}"
            ) from None

        if not items:
            warnings.warn(
                f"WARNING: {source} scraper returned no results. "
                f"Check that the profile is public and the URL is correct.",
                RuntimeWarning,
                stacklevel=2,
            )
            return []

        return items

    def scrape_linkedin(self, linkedin_url: str) -> List[Dict[str, Any]]:
        """Scrape one LinkedIn profile.

        Input schema of harvestapi/linkedin-profile-scraper:
        {"profileScraperMode": str, "queries": [profile url or public identifier]}
        """
        url = self._normalize_url(linkedin_url, "LinkedIn")

        run_input = {
            "profileScraperMode": "Profile details no email ($4 per 1k)",
            "queries": [url],
        }

        return self._run_actor(
            self.linkedin_actor_id,
            run_input,
            "LinkedIn",
        )

    def scrape_instagram(self, instagram_url: str) -> List[Dict[str, Any]]:
        """Scrape one Instagram profile.

        Input schema of apify/instagram-profile-scraper:
        {"usernames": [username, profile url or profile id]}
        """
        url = self._normalize_url(instagram_url, "Instagram")

        run_input = {"usernames": [url]}

        return self._run_actor(
            self.instagram_actor_id,
            run_input,
            "Instagram",
        )

    def scrape_person(
        self,
        linkedin_url: str,
        instagram_url: str,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Scrape both platforms and return the raw, untouched Apify output."""
        return {
            "linkedin": self.scrape_linkedin(linkedin_url),
            "instagram": self.scrape_instagram(instagram_url),
        }