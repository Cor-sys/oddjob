"""Central configuration, loaded from a .env file at the project root."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
PENDING_DIR = DATA_DIR / "pending"      # generated, awaiting your approval
PUBLISHED_DIR = DATA_DIR / "published"  # approved + posted
WORK_DIR = DATA_DIR / "work"            # scratch space during generation

for _d in (PENDING_DIR, PUBLISHED_DIR, WORK_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# A varied pool of free Microsoft voices rotated across videos so the channel
# doesn't always sound the same. Edit TTS_VOICES in .env to taste.
_DEFAULT_VOICE_POOL = (
    "en-US-AndrewMultilingualNeural,en-US-BrianMultilingualNeural,"
    "en-US-ChristopherNeural,en-US-EricNeural,en-GB-RyanNeural,en-AU-WilliamNeural"
)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY"))
    # quality model for the creative writing (script/story)
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-2.5-flash"))
    # cheaper model for the mechanical grounded data calls (trends + fact-check)
    gemini_calls_model: str = field(default_factory=lambda: _env("GEMINI_CALLS_MODEL", "gemini-2.5-flash-lite"))
    # used automatically when a model is overloaded (503) or rate-limited (429)
    gemini_fallback_model: str = field(default_factory=lambda: _env("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite"))

    pexels_api_key: str = field(default_factory=lambda: _env("PEXELS_API_KEY"))
    # single fallback voice (used if the pool is empty)
    tts_voice: str = field(default_factory=lambda: _env("TTS_VOICE", "en-US-AndrewMultilingualNeural"))
    # pool of voices rotated per video
    tts_voices: list[str] = field(default_factory=lambda: _csv(_env("TTS_VOICES", _DEFAULT_VOICE_POOL)))

    content_niche: str = field(default_factory=lambda: _env("CONTENT_NICHE"))
    # Topics matching these keywords are treated as inherently unverifiable
    # subject matter (UFOs/aliens/etc.): in `auto` mode they're allowed to
    # publish on a 'needs_review' fact-check verdict, since the claim can't be
    # confirmed — but a 'rejected' (actively debunked) verdict still blocks them.
    # Every other topic still requires a clean 'ok' to auto-publish.
    speculative_keywords: list[str] = field(default_factory=lambda: _csv(_env(
        "SPECULATIVE_KEYWORDS",
        "ufo,ufos,uap,uaps,alien,aliens,extraterrestrial,flying saucer,"
        "close encounter,abduction,roswell,paranormal,cryptid",
    )))
    content_tone: str = field(default_factory=lambda: _env(
        "CONTENT_TONE",
        "clear, punchy, plain-spoken explainer; smart and factual but neutral — no hype, no jokes",
    ))
    clip_seconds: int = field(default_factory=lambda: int(_env("CLIP_SECONDS", "25") or 25))

    youtube_client_secrets: str = field(default_factory=lambda: _env("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json"))
    youtube_token_file: str = field(default_factory=lambda: _env("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"))
    youtube_privacy: str = field(default_factory=lambda: _env("YOUTUBE_PRIVACY", "private"))
    # Optional channel/subscribe link auto-appended to every post description as a
    # CTA. Blank = nothing added (keeps the repo generic for other users). Use the
    # channel-ID URL (.../channel/UC...) — it survives handle changes.
    subscribe_url: str = field(default_factory=lambda: _env("SUBSCRIBE_URL"))
    subscribe_cta: str = field(default_factory=lambda: _env("SUBSCRIBE_CTA", "Subscribe for more"))

    facebook_page_id: str = field(default_factory=lambda: _env("FACEBOOK_PAGE_ID"))
    facebook_page_token: str = field(default_factory=lambda: _env("FACEBOOK_PAGE_TOKEN"))

    def require_gemini(self) -> None:
        if not self.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your "
                "Google AI Studio key from https://aistudio.google.com/apikey"
            )


settings = Settings()
