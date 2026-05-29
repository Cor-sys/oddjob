"""Upload a clip to YouTube as a Short via the YouTube Data API v3."""
from __future__ import annotations

from pathlib import Path

from ..config import ROOT, settings

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _find_client_secrets() -> Path | None:
    """Locate the OAuth client-secrets JSON. Uses the configured path if present,
    otherwise auto-detects any client_secret*.json dropped into secrets/ (so you
    don't have to rename Google's long download filename)."""
    configured = ROOT / settings.youtube_client_secrets
    if configured.exists():
        return configured
    secrets_dir = configured.parent
    if secrets_dir.is_dir():
        token_name = (ROOT / settings.youtube_token_file).name
        candidates = sorted(secrets_dir.glob("client_secret*.json")) or [
            p for p in secrets_dir.glob("*.json") if p.name != token_name
        ]
        if candidates:
            return candidates[0]
    return None


def _credentials():
    # Imported lazily so the rest of the app runs without Google libs installed.
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_file = ROOT / settings.youtube_token_file

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets_file = _find_client_secrets()
            if not secrets_file:
                raise RuntimeError(
                    f"No YouTube client-secrets JSON found in "
                    f"{(ROOT / settings.youtube_client_secrets).parent}. "
                    "Download it from Google Cloud Console (Desktop OAuth client) "
                    "and drop it in that folder."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


def upload(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str] | None = None,
    privacy: str | None = None,
) -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    privacy = privacy or settings.youtube_privacy
    # "#Shorts" + a vertical <60s clip is how YouTube classifies Shorts.
    if "#shorts" not in (title + description).lower():
        description = f"{description}\n\n#Shorts"

    youtube = build("youtube", "v3", credentials=_credentials())
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": (tags or [])[:15],
            "categoryId": "25",  # News & Politics
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    vid = response["id"]

    # Log the upload to the spend ledger. The Data API is free (quota-metered),
    # so this records 0 dollars and ~100 quota units — best-effort, never fatal.
    try:
        from .. import costs
        size = video_path.stat().st_size if video_path.exists() else None
        costs.record_youtube_upload(vid, file_size=size)
    except Exception:
        pass

    return {"platform": "youtube", "id": vid, "url": f"https://youtu.be/{vid}"}
