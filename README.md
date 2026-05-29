# Social Media Bot

An automated short-form video channel that runs **entirely in the cloud** and is **free to start**. It discovers trending stories, writes a punchy script, fact-checks it, generates a voiced + captioned vertical clip, and posts it to **YouTube** on a schedule — fully hands-off.

```
Google Search (grounded)
   → trend discovery (Gemini)
   → script + hook (Gemini)
   → fact-check / vet (Gemini + Google Search)
   → voiceover (edge-tts, free, 200+ voices)
   → b-roll (Pexels free / NASA public domain)
   → ffmpeg assemble  1080 × 1920 · 25s · hook overlay · fast cuts
   → auto-publish to YouTube  (3× / day via GitHub Actions, no server needed)
```

Everything except the Gemini API calls is **free with no meaningful limits**. Gemini has a free tier suitable for testing and light use; for a reliable 3-posts/day schedule a billing account is recommended — at roughly **$0.01–$0.02 per video** the cost is negligible.

---

## Can I run this for free?

**Yes, with caveats — here is the honest breakdown.**

Each video makes **3 Gemini API calls**:

| Call | Model | Uses Google Search grounding? |
|---|---|---|
| Trend discovery | gemini-2.5-flash-lite | Yes |
| Script writing | gemini-2.5-flash | No |
| Fact-check | gemini-2.5-flash-lite | Yes |

### Free tier limits (as of May 2026)

Source: [ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits) and [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)

| Service | Free limit | Notes |
|---|---|---|
| **Gemini 2.5 Flash** | 10 RPM · 250,000 TPM · **1,500 RPD** | Used for script writing |
| **Gemini 2.5 Flash-Lite** | 15 RPM · 250,000 TPM · **1,500 RPD** | Used for trends + fact-check |
| **Pexels** (b-roll footage) | 200 req/hr · 20,000 req/month | More than enough |
| **NASA** (space footage, keyless) | 30 req/hr · 50 req/day | Register a free key for 1,000 req/hr |
| **YouTube Data API v3** | 10,000 quota units/day · ~100 units/upload | ~100 uploads/day free |
| **edge-tts** (voiceover) | Unlimited — no key needed | Free Microsoft neural voices |
| **GitHub Actions** | 2,000 min/month (free accounts) | Each run takes ~3 min |

> Check your project's active limits at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit).

### The math

Each video uses **3 Gemini calls** (1 Flash + 2 Flash-Lite). At 1,500 RPD per model on the free tier:

| Scenario | Flash calls used | Flash-Lite calls used | % of free daily quota |
|---|---|---|---|
| 3 videos/day (default) | 3 | 6 | **< 1%** |
| 50 videos/day | 50 | 100 | ~7% |
| **Free ceiling** | ~1,500 | ~750 | 100% |

**Practical free limit: ~500 videos/day** before RPD becomes a concern. At the default 3/day you will never come close.

The limit you are more likely to hit in burst use is **RPM (10 requests/minute for Flash)**. If you generate a large batch very quickly — say 15+ videos at once — you may hit the per-minute cap. Spread across separate runs (as the GitHub Actions schedule does), this is never an issue.

For casual testing and normal use the free tier is entirely sufficient. Connecting a billing account primarily unlocks higher RPM ceilings and removes any throttling risk. **It does not change the cost much:**

### What it costs on a billed account

With billing enabled, Gemini Tier 1 includes **1,500 free grounding queries/day** — far more than enough. You only pay for token usage:

| Per video | Approx. cost |
|---|---|
| Trend discovery (flash-lite, grounded) | < $0.01 |
| Script writing (flash) | < $0.01 |
| Fact-check (flash-lite, grounded) | < $0.01 |
| **Total per video** | **~$0.01 – $0.02** |
| Voiceover, footage, YouTube posting | **$0.00** (always free) |

**At 3 videos/day that is roughly $1–2/month.** YouTube's Data API, Pexels, edge-tts, and NASA remain free regardless of posting volume.

> The bot logs every API call to `data/costs.jsonl`. Run `python -m socialbot.cli costs` to see a live breakdown by stage and model.

---

## Customize for your use case

Everything is controlled by your `.env` file. You do not need to touch any code.

### Business / brand channel
Promote your products, services, or industry. The bot will source relevant news and angle it toward your niche.

```env
CONTENT_NICHE=sustainable fashion brand, eco-friendly clothing, ethical manufacturing trends
CONTENT_TONE=confident and brand-forward, speaks to eco-conscious consumers, warm but informative
CLIP_SECONDS=20
```

### Local news / community channel
```env
CONTENT_NICHE=local government, city council, community events, regional business news
CONTENT_TONE=neutral local news reporter, factual, community-focused, no sensationalism
CLIP_SECONDS=30
```

### Finance / investing
```env
CONTENT_NICHE=stock market, crypto, personal finance, economic news, Federal Reserve
CONTENT_TONE=calm and analytical, data-driven, speaks to retail investors, avoids hype
CLIP_SECONDS=25
```

### Fitness / health
```env
CONTENT_NICHE=fitness research, nutrition science, sports medicine, workout trends
CONTENT_TONE=motivating but evidence-based, plain-spoken, practical — no bro-science
CLIP_SECONDS=20
```

### Gaming / esports
```env
CONTENT_NICHE=video game releases, esports tournaments, gaming industry news, game reviews
CONTENT_TONE=enthusiastic and casual, speaks to gamers, fast-paced, current slang is fine
CLIP_SECONDS=20
```

### Tech / AI (the default)
```env
CONTENT_NICHE=AI and emerging technology, space and astronomy, UFOs/UAPs and the search for extraterrestrial life
CONTENT_TONE=clear, punchy, plain-spoken explainer; smart and factual but neutral — no hype, no jokes
CLIP_SECONDS=25
```

### Key parameters

| Variable | What it controls | Example |
|---|---|---|
| `CONTENT_NICHE` | What topics Gemini searches for. Be specific — the more concrete, the better the stories. | `"electric vehicles, Tesla, EV charging infrastructure"` |
| `CONTENT_TONE` | The narrator's personality and speaking style. Shapes every word of the script. | `"upbeat fitness coach, motivating, science-backed"` |
| `CLIP_SECONDS` | Target video length. 15–30s is the sweet spot for Shorts retention. | `20` |
| `TTS_VOICES` | Comma-separated pool of Microsoft neural voices. The bot rotates through them. Run `python -m socialbot.cli voices` to list all options. | `"en-US-AndrewMultilingualNeural,en-GB-RyanNeural"` |
| `YOUTUBE_PRIVACY` | `public`, `unlisted`, or `private`. Set to `private` while testing. | `public` |
| `SPECULATIVE_KEYWORDS` | Topics that are allowed to post on a `needs_review` fact-check (inherently unverifiable claims). | `"ufo,alien,paranormal"` |

---

## Setup

### 1. Prerequisites

- **Python 3.11+**
- **ffmpeg** on your PATH — `ffmpeg -version` should work

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # Windows: copy .env.example .env
```

Edit `.env` and fill in the keys below.

### 3. API keys

#### Gemini — Google AI Studio (required)
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and create an API key.
2. Set `GEMINI_API_KEY=` in `.env`.

The free tier works for testing. For a reliable daily schedule, connect a billing account at [console.cloud.google.com](https://console.cloud.google.com) — the same API key works, billing just unlocks higher limits and 1,500 free grounding queries/day.

#### Pexels b-roll (optional, free)
Without this key you get an animated gradient background. With it you get real footage:
1. Create a free account at [pexels.com/api](https://www.pexels.com/api/) and copy your key.
2. Set `PEXELS_API_KEY=` in `.env`.

#### NASA footage (optional, free)
Space/astronomy/UAP topics automatically pull from NASA's public-domain library. No key is required, but registering a free key at [api.nasa.gov](https://api.nasa.gov/) raises the rate limit from 50 to 1,000 req/hr — recommended if your niche is space-heavy.

#### YouTube (to publish)
1. Open [console.cloud.google.com](https://console.cloud.google.com) and create (or reuse) a project.
2. **APIs & Services → Enable APIs → YouTube Data API v3 → Enable.**
3. **OAuth consent screen:** External. Add yourself as a test user.
4. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
5. Download the JSON and save it as `secrets/youtube_client_secret.json`.
6. Run `python -m socialbot.cli youtube-auth` once to authorize via browser. The token refreshes automatically after that.
7. Keep `YOUTUBE_PRIVACY=private` while you're testing.

> **OAuth note:** keep the app in **Testing** status for personal use (token is permanent). If you switch to Production, the Testing token expires after 7 days and you'll need to re-run `youtube-auth`.

#### Facebook (optional)
1. Create a Facebook **Page** if you don't have one.
2. At [developers.facebook.com](https://developers.facebook.com) create a Business app and add the Graph API.
3. Generate a Page access token with `pages_manage_posts` + `pages_read_engagement`, then exchange it for a **long-lived** token.
4. Set `FACEBOOK_PAGE_ID=` and `FACEBOOK_PAGE_TOKEN=` in `.env`.

---

## Usage

```bash
# See available AI voices
python -m socialbot.cli voices

# Preview today's trending topics (no video built)
python -m socialbot.cli trends --count 5

# Full pipeline: discover → script → fact-check → render
python -m socialbot.cli generate --count 3

# Browse the review queue
python -m socialbot.cli list
python -m socialbot.cli show <id>    # script, fact-check verdict, sources
python -m socialbot.cli open <id>    # play the clip

# Approve or reject
python -m socialbot.cli approve <id>
python -m socialbot.cli reject  <id> --reason "off-brand"

# Publish approved items
python -m socialbot.cli publish <id> --targets youtube
python -m socialbot.cli publish --all-approved

# Fully automated: generate + fact-check + auto-publish in one shot
python -m socialbot.cli auto --count 3 --targets youtube

# Cost tracking
python -m socialbot.cli costs            # breakdown by stage + model
python -m socialbot.cli costs --youtube  # YouTube quota usage
python -m socialbot.cli costs --json     # machine-readable
```

---

## Automated scheduling (GitHub Actions)

The bot runs headless on GitHub's servers — **no server, no always-on machine**. Each run spins up, generates and posts a clip, then shuts down. The free Actions tier covers 3 posts/day easily.

### Setup
1. Push this repo to a **private** GitHub repository.
2. Add these **Secrets** under *Settings → Secrets and variables → Actions*:

   | Secret | Value |
   |---|---|
   | `GEMINI_API_KEY` | Your Gemini API key |
   | `PEXELS_API_KEY` | Your Pexels key |
   | `YOUTUBE_TOKEN` | Contents of `secrets/youtube_token.json` after running `youtube-auth` locally |

3. The workflow (`.github/workflows/auto.yml`) fires at **13:00, 18:00, and 23:00 UTC** by default. Edit the `cron` lines to match your timezone.
4. Trigger a run manually from the **Actions** tab → **auto-post** → **Run workflow** to test.

### Pausing
Disable the workflow in the Actions tab. A local `data/PAUSED` file pauses local runs only — it has no effect on the cloud job.

---

## How the pipeline works

```
Trend discovery
  Gemini + Google Search finds stories trending in the last 24-48 hours,
  filtered to your CONTENT_NICHE. Returns a batch of topics; picks the
  freshest ones not covered recently (topic dedup via data/used_topics.json).

Script + hook
  Gemini writes a ~25s voiceover. The first sentence is the single most
  surprising fact (scroll-stopper hook). Ends on a line that loops cleanly
  back into the hook so auto-replays feel seamless.

Fact-check
  A second grounded Gemini call verifies every material claim:
    ok           → auto-publishes
    needs_review → held, unless the topic is inherently speculative
                   (UFO/alien/UAP) — then it publishes
    rejected     → never published (claims actively contradicted by sources)

Visuals
  Space/astronomy/UAP topics → NASA public-domain imagery + video
  Everything else             → Pexels stock footage
  Footage is deduplicated across runs so clips never reuse the same b-roll.

Assembly (ffmpeg, 1080×1920)
  • Hook text burns in big + centered for the first ~2.5s
  • B-roll cuts every ~3s (the algorithm favors fast visual changes)
  • Word-synced captions burned in (white, high-contrast)
  • Voices rotate from a pool so every clip sounds different
```

---

## Responsible use

- **You are responsible** for what gets posted. The fact-check pass reduces risk but is not a guarantee.
- Respect YouTube and Facebook automation and spam policies.
- Pexels footage is licensed for free commercial use. NASA imagery is public domain.
- edge-tts uses Microsoft Edge's Read Aloud feature — free for personal and small-scale use.
