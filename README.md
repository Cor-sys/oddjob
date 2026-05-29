# social media bot

Scrapes trending topics, writes a short script, **fact-checks it**, turns it into a
vertical short-form clip (AI voiceover + captions + footage), drops it into a
**review queue for you to approve**, then publishes approved clips to **YouTube**
and **Facebook**.

```
trends (Gemini + Google Search)
   -> script (Gemini)
   -> fact-check / vet (Gemini + Google Search)
   -> voiceover (edge-tts, free) + captions + b-roll (Pexels, free)
   -> ffmpeg assemble (1080x1920)
   -> PENDING queue  --you approve-->  publish to YouTube + Facebook
```

Nothing is ever posted automatically — a human approves every clip first.

---

## 1. Prerequisites

- **Python 3.11+** (tested on 3.14)
- **ffmpeg** on your PATH (`ffmpeg -version` should work) — already installed here.

## 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Then edit `.env` and fill in keys (see below).

## 3. Keys & accounts

### Gemini (required) — Google AI Studio
1. Go to https://aistudio.google.com/apikey and create an API key.
2. Put it in `.env` as `GEMINI_API_KEY=...`.

### Pexels b-roll (optional, free)
Without this you get a clean animated gradient background. For real footage:
1. Create a free account at https://www.pexels.com/api/ and copy your key.
2. `PEXELS_API_KEY=...` in `.env`.

### YouTube Data API (to publish to YouTube)
1. In https://console.cloud.google.com create a project (or reuse one).
2. **APIs & Services → Enable APIs → "YouTube Data API v3" → Enable.**
3. **OAuth consent screen:** External, add yourself as a Test user.
4. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
5. Download the JSON and save it as `secrets/youtube_client_secret.json`
   (or point `YOUTUBE_CLIENT_SECRETS` at it).
6. First publish opens a browser to authorize; the token is cached afterward.
7. Keep `YOUTUBE_PRIVACY=private` until you've watched a few results.

### Facebook Page (to publish to Facebook)
1. Create a Facebook **Page** if you don't have one.
2. At https://developers.facebook.com create an app (type: Business).
3. Add the **Graph API**; use the Graph API Explorer to generate a Page access
   token with `pages_manage_posts` + `pages_read_engagement`, then exchange it
   for a **long-lived** token.
4. Put the Page's numeric ID and token in `.env`:
   `FACEBOOK_PAGE_ID=...`, `FACEBOOK_PAGE_TOKEN=...`.

## 4. Usage

```powershell
# list AI voices, pick one for TTS_VOICE in .env
python -m socialbot.cli voices

# just see what's trending (no video built)
python -m socialbot.cli trends --count 5 --niche "space"

# full pipeline: discover -> script -> fact-check -> render to the review queue
python -m socialbot.cli generate --count 3

# review the queue
python -m socialbot.cli list
python -m socialbot.cli show <id>      # narration, fact-check verdict, sources
python -m socialbot.cli open <id>      # play the clip

# decide
python -m socialbot.cli approve <id>
python -m socialbot.cli reject  <id> --reason "off-brand"

# publish (only works on approved items)
python -m socialbot.cli publish <id> --targets youtube,facebook
python -m socialbot.cli publish --all-approved
```

Generated clips live in `data/pending/<id>/`; published ones move to
`data/published/<id>/`. Each folder has a `meta.json` with the script, the
fact-check report, and the publish results.

## 5. Scheduling (optional)

Run `generate` on a schedule (Windows Task Scheduler) to keep the review queue
full; you approve and publish on your own cadence. Auto-publishing is deliberately
not wired up — keep the human in the loop.

## Notes & responsibilities

- **You are responsible** for what gets posted. The fact-check pass reduces risk
  but is not a guarantee — that's why every clip needs your approval.
- Respect YouTube/Facebook automation & spam policies and the licensing of any
  footage. Pexels content is free to use; AI-generated voiceover via edge-tts is
  fine for this use.
- Start with private/unlisted uploads while you tune the output.
```
