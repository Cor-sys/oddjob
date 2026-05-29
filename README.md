# Social Media Bot

An automated short-form video channel that **runs entirely in the cloud for free** (or near-free at scale). It discovers trending stories, writes a punchy script, fact-checks it, generates a voiced + captioned vertical clip, and posts it to **YouTube** — fully hands-off, on a schedule.

```
Google Search (grounded)
   → trend discovery (Gemini)
   → script + hook (Gemini)
   → fact-check / vet (Gemini + Google Search)
   → voiceover (edge-tts, free, 200+ voices)
   → b-roll (Pexels free / NASA public domain)
   → ffmpeg assemble  1080 × 1920 · 25s · hook overlay · fast cuts
   → auto-publish to YouTube  (3× / day on GitHub Actions, no server needed)
```

Everything except the AI text generation is **free with no limits**. The Gemini API has a free tier that covers light usage, and even at full 3-posts/day automation the cost is well under $2/month.

---

## Can I really run this for free?

**Yes — here's the math.**

Each video the bot generates makes **3 Gemini API calls**:

| Call | Model | Purpose |
|---|---|---|
| Trend discovery | gemini-2.5-flash-lite | Find today's story (shared across a batch) |
| Script | gemini-2.5-flash | Write the narration + hook |
| Fact-check | gemini-2.5-flash-lite | Verify claims against live search |

### Free tier limits (as of 2026)

| Service | Free allowance | Resets |
|---|---|---|
| **Gemini 2.5 Flash** | ~500 req/day · 10 RPM | Daily midnight PT |
| **Gemini 2.5 Flash-Lite** | ~1,000 req/day · 15 RPM | Daily midnight PT |
| **Pexels** (b-roll) | 200 req/hr · 20,000 req/month | Monthly |
| **NASA** (space footage, keyless) | 30 req/hr · 50 req/day per IP | Hourly |
| **YouTube Data API v3** | 10,000 quota units/day · ~100 units/upload | Daily midnight PT |
| **edge-tts** (voiceover) | Unlimited — no key needed | — |
| **GitHub Actions** | 2,000 minutes/month (free accounts) | Monthly |

> **Note:** Google adjusts Gemini free-tier limits periodically. Check your current limits at [aistudio.google.com](https://aistudio.google.com) under **Rate limits**.

### How many videos can you make for free?

At 3 calls per video:

- **Gemini Flash** (scripts): ~500 RPD ÷ 1 call/video = **~500 videos/day headroom**
- **Gemini Flash-Lite** (trends + fact-check): ~1,000 RPD ÷ 2 calls/video = **~500 videos/day headroom**
- **YouTube uploads**: 10,000 units ÷ 100 units/upload = **100 uploads/day free**
- **GitHub Actions**: 2,000 min/month ÷ ~3 min/run = **~660 runs/month free**

**Practical free ceiling: ~100 videos/day.** The default schedule (3/day) uses less than 1% of the free quota — you'd have to be posting aggressively before any limit becomes relevant.

The one exception: **NASA footage** uses a keyless demo key capped at 50 requests/day. For space-heavy content, [register a free NASA API key](https://api.nasa.gov/) to get 1,000 req/hr.

### What does it cost once you're past the free tier?

If you connect a billing account (required for scheduled/production use with heavier volume), pricing is per token:

| What happens per video | Approx. cost |
|---|---|
| Trend discovery (flash-lite, grounded) | < $0.01 |
| Script writing (flash) | < $0.01 |
| Fact-check (flash-lite, grounded) | < $0.01 |
| **Total per video** | **~$0.01 – $0.02** |
| Voiceover, footage, publishing | **$0.00** |

At 3 videos/day that's roughly **$1–2/month** — less than a coffee. YouTube's Data API, Pexels, edge-tts, and NASA all remain free regardless of volume.

> Costs are estimated at published Gemini list prices and will vary with token usage per topic. The bot logs every call to `data/costs.jsonl`; run `python -m socialbot.cli costs` to see a live breakdown.

---

## Setup

### 1. Prerequisites

- **Python 3.11+**
- **ffmpeg** on your PATH — `ffmpeg -version` should work

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

Edit `.env` and fill in the keys below.

### 3. API keys

#### Gemini — Google AI Studio (required)
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and create an API key.
2. Set `GEMINI_API_KEY=` in `.env`.

For casual use the free tier is enough. For 3+ posts/day on a schedule, connect a billing account — the API key stays the same, billing just enables higher rate limits.

#### Pexels b-roll (optional, free)
Without this you get a clean animated gradient background. With it you get real footage:
1. Create a free account at [pexels.com/api](https://www.pexels.com/api/) and copy your key.
2. Set `PEXELS_API_KEY=` in `.env`.

#### NASA footage (optional, free)
Space/astronomy/UAP topics automatically pull from NASA's public-domain library. No key needed, but registering a free key at [api.nasa.gov](https://api.nasa.gov/) raises the rate limit from 50 to 1,000 req/hr.

#### YouTube (to publish)
1. Open [console.cloud.google.com](https://console.cloud.google.com) and create (or reuse) a project.
2. **APIs & Services → Enable APIs → YouTube Data API v3 → Enable.**
3. **OAuth consent screen:** External. Add yourself as a test user.
4. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
5. Download the JSON and save it as `secrets/youtube_client_secret.json`.
6. Run `python -m socialbot.cli youtube-auth` once to authorize via browser. The token is cached and refreshed automatically after that.
7. Keep `YOUTUBE_PRIVACY=private` in `.env` while you're testing.

> **Important:** keep the OAuth app in **Testing** status for personal use. If you publish it to Production, the token is permanent — in Testing it expires after 7 days and you'll need to re-run `youtube-auth`.

#### Facebook (optional)
1. Create a Facebook **Page** if you don't have one.
2. At [developers.facebook.com](https://developers.facebook.com) create a Business app and add the Graph API.
3. Use the Graph API Explorer to generate a Page access token with `pages_manage_posts` + `pages_read_engagement`, then exchange it for a **long-lived** token.
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

# Publish (only works on approved items)
python -m socialbot.cli publish <id> --targets youtube
python -m socialbot.cli publish --all-approved

# Fully automated: generate + fact-check + auto-publish in one command
python -m socialbot.cli auto --count 3 --targets youtube

# Cost tracking
python -m socialbot.cli costs            # breakdown by stage + model
python -m socialbot.cli costs --youtube  # YouTube quota usage
python -m socialbot.cli costs --json     # machine-readable
```

Generated clips live in `data/pending/<id>/`. Each folder contains `meta.json` with the script, fact-check report, generation cost, and publish results.

---

## Automated scheduling (GitHub Actions)

The bot runs headless in GitHub Actions — **no server, no always-on machine**. Each run spins up, generates a clip, posts it, and shuts down. The free Actions tier easily covers 3 posts/day.

### Setup
1. Fork or push this repo to a **private** GitHub repository.
2. Add these **Secrets** under *Settings → Secrets and variables → Actions*:

   | Secret | Value |
   |---|---|
   | `GEMINI_API_KEY` | Your Gemini API key |
   | `PEXELS_API_KEY` | Your Pexels key |
   | `YOUTUBE_TOKEN` | Contents of `secrets/youtube_token.json` after running `youtube-auth` locally |

3. The workflow (`.github/workflows/auto.yml`) runs at **13:00, 18:00, and 23:00 UTC** by default. Edit the cron lines to match your target timezone.
4. You can also trigger a run manually from the **Actions** tab → **auto-post** → **Run workflow**.

### Pausing
To pause cloud posting, disable the workflow in the repo's Actions tab. A `data/PAUSED` file pauses **local** runs but has no effect on Actions.

---

## How the content pipeline works

```
Trend discovery
  Gemini searches Google for stories trending in the last 24-48h
  filtered to your CONTENT_NICHE. Returns ~5 topics; picks the
  freshest ones not covered recently (topic dedup).

Script + hook
  Gemini writes a ~25s voiceover that leads with the single most
  surprising fact (scroll-stopper hook), delivers the key details,
  and ends on a line that loops cleanly into the hook on replay.

Fact-check
  A second grounded call verifies every material claim. Verdict:
    ok           → publishes automatically
    needs_review → held (unless the topic is inherently speculative
                   — UFOs/aliens/UAPs — in which case it publishes)
    rejected     → never published (actively contradicted by sources)

Visuals
  Space/astronomy/UAP topics → NASA public-domain imagery + video
  Everything else             → Pexels stock footage
  Footage is deduplicated across runs so clips don't reuse the same b-roll.

Assembly (ffmpeg, 1080×1920)
  • Hook text burns in big + centered for the first ~2.5s
  • B-roll cuts every ~3s (algorithm favors fast visual changes)
  • Word-synced captions (white, burned-in)
  • Voices rotate from a pool so every clip sounds different
```

---

## Responsible use

- **You are responsible** for what gets posted. The fact-check pass reduces risk but is not a guarantee.
- Respect YouTube and Facebook automation policies.
- Pexels footage is licensed for free use with attribution. NASA imagery is public domain.
- edge-tts voices are used via Microsoft Edge's Read Aloud feature — free for personal use.
