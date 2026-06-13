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
RESERVE_DIR = DATA_DIR / "reserve"      # tournament runners-up, banked as recipes
WORK_DIR = DATA_DIR / "work"            # scratch space during generation

for _d in (PENDING_DIR, PUBLISHED_DIR, RESERVE_DIR, WORK_DIR):
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
        "a sharp, curious narrator who makes you lean in: vivid, specific, and "
        "confident, telling a real story rather than reciting facts; precise and "
        "grounded, never hype or clickbait",
    ))
    # v2 mini-docs run a touch longer than the old 25s explainers. A per-video
    # jitter between MIN and MAX (Phase 5) creates real length experiment-arms.
    clip_seconds: int = field(default_factory=lambda: int(_env("CLIP_SECONDS", "38") or 38))
    clip_seconds_min: int = field(default_factory=lambda: int(_env("CLIP_SECONDS_MIN", "30") or 30))
    clip_seconds_max: int = field(default_factory=lambda: int(_env("CLIP_SECONDS_MAX", "45") or 45))

    # Branding: a small persistent corner logo "bug" + a brief logo outro card.
    # NO front-loaded intro (a logo before the hook kills Shorts retention).
    branding_enabled: bool = field(default_factory=lambda: _env("BRANDING_ENABLED", "true").lower() != "false")
    logo_path: str = field(default_factory=lambda: _env("LOGO_PATH", "assets/oddjob-logo.png"))
    logo_opacity: float = field(default_factory=lambda: float(_env("LOGO_OPACITY", "0.85") or 0.85))
    logo_scale_w: int = field(default_factory=lambda: int(_env("LOGO_SCALE_W", "150") or 150))
    # Length of the full-screen logo outro card; 0 = no outro card (the video
    # just ends on the last beat). Corner logo bug is controlled by BRANDING_ENABLED.
    endcard_seconds: float = field(default_factory=lambda: float(_env("ENDCARD_SECONDS", "0") or 0))
    # Music bed (off until a monetization-safe track is dropped in MUSIC_DIR).
    music_enabled: bool = field(default_factory=lambda: _env("MUSIC_ENABLED", "false").lower() == "true")
    music_dir: str = field(default_factory=lambda: _env("MUSIC_DIR", "assets/music"))
    music_volume: float = field(default_factory=lambda: float(_env("MUSIC_VOLUME", "0.08") or 0.08))

    youtube_client_secrets: str = field(default_factory=lambda: _env("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json"))
    youtube_token_file: str = field(default_factory=lambda: _env("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"))
    youtube_privacy: str = field(default_factory=lambda: _env("YOUTUBE_PRIVACY", "private"))

    facebook_page_id: str = field(default_factory=lambda: _env("FACEBOOK_PAGE_ID"))
    facebook_page_token: str = field(default_factory=lambda: _env("FACEBOOK_PAGE_TOKEN"))

    # ── v2 best-of-N tournament + daily batch (Phase 3) ─────────────────────
    # One daily batch mines many concepts, develops the strongest, and posts the
    # best `posts_per_day`; survivors that clear fact-check + a score floor are
    # banked as re-renderable recipes. Sized to fit the free tier (~20 Flash +
    # ~20 Flash-Lite requests/day). See the budget table in the v2 plan.
    posts_per_day: int = field(default_factory=lambda: int(_env("POSTS_PER_DAY", "3") or 3))
    # Per-batch funnel, sized for the split daily schedule (2 small batches/day, see
    # .github/workflows/auto.yml). Each batch mines a small pool, DEVELOPS a few, and
    # posts its share — Run A posts 2, Run B posts 1 (= POSTS_PER_DAY total). Mining
    # is 1 cheap call regardless of count; developing (research + draft) is the cost,
    # so develop_n is the main cost/footprint dial. 3/batch -> 6 develops/day, best of
    # 3 posted per batch. Smaller per-run bursts are also less bot-like ("less sus").
    concepts_n: int = field(default_factory=lambda: int(_env("CONCEPTS_N", "6") or 6))
    develop_n: int = field(default_factory=lambda: int(_env("DEVELOP_N", "3") or 3))
    # How many top unused concepts from the topic bank to merge into each batch's
    # scoring pool (Phase 4 — strong ideas we didn't have room to make carry over).
    bank_merge_n: int = field(default_factory=lambda: int(_env("BANK_MERGE_N", "5") or 5))
    finalists_n: int = field(default_factory=lambda: int(_env("FINALISTS_N", "4") or 4))
    # Quality gates on the anchored 0-100 judge score (Stage 2 — execution).
    #  - post_score_floor: a winner must clear this to be POSTED. Below it we fill
    #    the slot from a stronger reserve recipe, else post fewer (no filler).
    #  - bank_score_floor: lower bar — worth banking for a future slot even if not
    #    good enough to post today. A run can raise the post floor from learned
    #    analytics (strategy.post_floor); see tournament._effective_post_floor.
    post_score_floor: float = field(default_factory=lambda: float(_env("POST_SCORE_FLOOR", "65") or 65))
    bank_score_floor: float = field(default_factory=lambda: float(_env("BANK_SCORE_FLOOR", "55") or 55))
    # Soft ceiling on LLM calls in one batch — stop developing new concepts past
    # this so retries never blow the free-tier daily limit.
    batch_call_ceiling: int = field(default_factory=lambda: int(_env("BATCH_CALL_CEILING", "36") or 36))
    # Reserve bank: keep at most this many recipes; re-vet any older than N days
    # before re-rendering (1 grounded call, off the daily budget).
    reserve_max: int = field(default_factory=lambda: int(_env("RESERVE_MAX", "30") or 30))
    reserve_revet_days: int = field(default_factory=lambda: int(_env("RESERVE_REVET_DAYS", "7") or 7))
    # Native YouTube publishAt scheduling: the times of day to stagger the daily
    # posts at, interpreted in SCHEDULE_TZ (default UTC). One slot per post.
    publish_times: list[str] = field(default_factory=lambda: _csv(_env("PUBLISH_TIMES", "14:00,19:00,00:00")))
    schedule_tz: str = field(default_factory=lambda: _env("SCHEDULE_TZ", "UTC") or "UTC")

    def require_gemini(self) -> None:
        if not self.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Copy .env.example to .env and add your "
                "Google AI Studio key from https://aistudio.google.com/apikey"
            )


settings = Settings()
