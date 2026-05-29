# Social Media Bot

An automated short-form video channel that runs **entirely in the cloud** and is **free to start**. It finds trending stories (or uses topics you provide), writes a punchy script, fact-checks it, renders a voiced and captioned 9:16 clip, and posts it to **YouTube** automatically on a schedule — no server, no manual work.

```
Trend discovery  OR  your own topic
   → script + scroll-stopping hook  (Gemini 2.5 Flash)
   → fact-check against live sources  (Gemini 2.5 Flash-Lite + Google Search)
   → voiceover  (edge-tts — free Microsoft neural voices, rotated per video)
   → b-roll footage  (Pexels free tier  /  NASA public domain)
   → ffmpeg assembly  1080 × 1920 · configurable length · hook text overlay · fast cuts
   → auto-publish to YouTube  (3 × /day via GitHub Actions — no server needed)
```

---

## Is it free? The complete honest breakdown

### What is always free — no limits

| Service | What it does | Cost |
|---|---|---|
| **edge-tts** | AI voiceover — 200+ Microsoft neural voices | Free, no key |
| **NASA API** | Space/astronomy/UAP footage, public domain | Free, no key (register for higher limits) |
| **Pexels API** | Stock footage for non-space topics | Free tier: 200 req/hr, 20,000/month |
| **YouTube Data API v3** | Uploading videos | Free: 10,000 quota units/day, ~100 units/upload = **100 free uploads/day** |
| **GitHub Actions** | Running the schedule | Free tier: 2,000 min/month (~660 runs) |
| **ffmpeg** | Video assembly | Free, open source |

### Gemini API — the only paid dependency

The Gemini API is the only service that has a meaningful free limit. Everything else is effectively unlimited for this use case.

**Verified free tier limits** (read directly from AI Studio → Rate Limit dashboard):

| Model | RPM | RPD | Used for |
|---|---|---|---|
| Gemini 2.5 Flash | 5 | **20** | Script writing (1 call/video) |
| Gemini 2.5 Flash-Lite | 10 | **20** | Trend discovery + fact-check (2 calls/video) |
| Google Search grounding | — | **500** | Separate quota for the live-web search tool |

> Always verify your own project's limits at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) — Google adjusts these without notice.

---

### Free tier math: how many videos can you make?

Each video uses exactly **3 Gemini calls**:

| Call | Model | Count |
|---|---|---|
| Trend discovery | Flash-Lite (grounded) | 1 per **batch** (shared across all videos in a run) |
| Script writing | Flash | 1 per video |
| Fact-check | Flash-Lite (grounded) | 1 per video |

The Flash-Lite RPD of 20 is the binding constraint. With B batch runs per day generating V total videos:

```
Flash-Lite used = B (trend calls) + V (factcheck calls) ≤ 20
Flash used      = V (script calls) ≤ 20

Maximum V = 20 − B
```

| Runs/day | Max free videos | Flash used | Flash-Lite used |
|---|---|---|---|
| 1 run | **19 videos** | 19 / 20 | 20 / 20 |
| 3 runs (default) | **17 videos** | 17 / 20 | 20 / 20 |
| 5 runs | **15 videos** | 15 / 20 | 20 / 20 |

**The absolute free ceiling is 19 videos/day** (one single batch run).

> **Custom topics use only 2 calls per video** (no trend discovery call) — so with `--topic` mode the ceiling is a flat 20 videos/day.

### Recommended free setup

The default schedule — **3 runs × 2 videos = 6 generated per day, ~4 published** after the fact-check gate holds 1–2 — is deliberately conservative:

| | Flash RPD | Flash-Lite RPD | Headroom left |
|---|---|---|---|
| 3 runs × 2 videos | 6 / 20 **(30%)** | 9 / 20 **(45%)** | 14 Flash · 11 Flash-Lite |
| Your test run on top | +1 / 20 | +2 / 20 | 13 Flash · 9 Flash-Lite |

You can run one manual test on top of the schedule without hitting limits. Avoid running multiple test runs on the same day as your scheduled posts.

---

### Cost after the free tier

Connect a billing account and the daily RPD jumps to ~10,000 for Flash and **Unlimited** for Flash-Lite. You only pay for token usage — all other services remain free.

**Gemini list prices (May 2026):**

| Model | Input | Output |
|---|---|---|
| Gemini 2.5 Flash | $0.30 / 1M tokens | $2.50 / 1M tokens |
| Gemini 2.5 Flash-Lite | $0.10 / 1M tokens | $0.40 / 1M tokens |

**Cost per video** (based on actual measured token usage):

| Call | Model | Typical cost |
|---|---|---|
| Script | Flash | ~$0.002 |
| Fact-check | Flash-Lite | ~$0.001 |
| Trend discovery (shared) | Flash-Lite | ~$0.001 per batch |
| **Total per video** | | **~$0.003 – $0.013** |
| Voiceover, footage, YouTube | | **$0.00** |

**Monthly cost at different volumes:**

| Videos/day | Videos/month | Est. monthly cost |
|---|---|---|
| 4 (default ~4 published) | ~120 | **~$0.50** |
| 10 | 300 | **~$1.50** |
| 17 (max) | 510 | **~$3.00** |

> On Tier 1 (billing enabled), Google includes **1,500 free grounding queries/day**. At 2 grounded calls × 17 videos = 34/day, all grounding stays free. You only pay token costs.

---

## Setup

### 1. Prerequisites

- Python 3.11+
- ffmpeg on your PATH (`ffmpeg -version` should work)

### 2. Install

```bash
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env         # Windows: copy .env.example .env
```

Edit `.env` and fill in your keys (see below).

### 3. Keys and accounts

#### Gemini — Google AI Studio (required)
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and create a key.
2. Set `GEMINI_API_KEY=` in `.env`.

The **free tier works** for the default 3/day schedule and casual testing. For heavier use or maximum reliability, connect a billing account — the same key works, billing just unlocks higher RPD and removes throttling risk. See cost table above for what it actually charges.

#### Pexels (optional, free)
Without this, non-space videos get an animated gradient background. With it, you get real stock footage:
1. Create a free account at [pexels.com/api](https://www.pexels.com/api/) and copy your key.
2. Set `PEXELS_API_KEY=` in `.env`.

#### NASA footage (optional, free)
Space, astronomy, and UAP topics automatically pull from NASA's public-domain image and video library. No key required — but registering a free key at [api.nasa.gov](https://api.nasa.gov/) raises the rate limit from 50 req/day (keyless) to 1,000 req/hr. Recommended if your content is space-heavy.

#### YouTube (required to publish)
1. Open [console.cloud.google.com](https://console.cloud.google.com) and create (or reuse) a project.
2. **APIs & Services → Enable APIs → YouTube Data API v3 → Enable.**
3. **OAuth consent screen → External.** Add yourself as a test user.
4. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
5. Download the JSON and save it as `secrets/youtube_client_secret.json`.
6. Run `python -m socialbot.cli youtube-auth` once to open a browser login. The token is saved and auto-refreshed after that.
7. Set `YOUTUBE_PRIVACY=private` in `.env` while testing.

> **Keep the OAuth app in Testing status** for personal use. In Testing, tokens are permanent. If you publish to Production, the token expires after 7 days and you'll need to re-run `youtube-auth`.

#### Facebook (optional)
1. Create a Facebook **Page**.
2. At [developers.facebook.com](https://developers.facebook.com) create a Business app and add the Graph API.
3. Generate a Page access token with `pages_manage_posts` + `pages_read_engagement`. Exchange it for a long-lived token.
4. Set `FACEBOOK_PAGE_ID=` and `FACEBOOK_PAGE_TOKEN=` in `.env`.

---

## Usage

### Trend-based videos (automated)

```bash
# Preview today's trending topics — no video rendered, costs 1 Flash-Lite call
python -m socialbot.cli trends --count 5

# Generate from trends → queue for review
python -m socialbot.cli generate --count 3
python -m socialbot.cli generate --count 3 --niche "electric vehicles"

# Fully automated: generate + fact-check + publish in one command
python -m socialbot.cli auto --count 2 --targets youtube
```

### Custom topic videos (your own content)

Skip trend discovery and make a video about anything — a product launch, a news story you researched, a promotion, anything.

```bash
# Generate a custom video → lands in review queue for you to approve
python -m socialbot.cli generate \
  --topic "Our Summer Sale Is Live" \
  --facts "20% off all products June 1-30, free shipping over $50, use code SUMMER" \
  --keywords "shopping,sale,discount,retail"

# Generate AND auto-publish a custom video in one shot
python -m socialbot.cli auto \
  --topic "New Product Launch: Model X" \
  --facts "Model X launches today at $299. Key features: 10hr battery, waterproof, AI-powered." \
  --keywords "product,technology,launch" \
  --targets youtube
```

Custom topics skip trend discovery (saves 1 API call) and are published even if the fact-checker returns `needs_review` — since you wrote the content, unverifiable promotional claims are expected. Only a `rejected` verdict (content actively debunked by sources) blocks publishing.

> **Note:** Custom `--topic` + `--facts` are for automation only, not for storing in your `.env`. Set `CONTENT_NICHE` and `CONTENT_TONE` in `.env` for your channel's ongoing focus.

### Promote your own content — songs, products, links (`promo`)

The `promo` command builds a post from **your own media** — your song, your product shot, your video — with a **clickable link** in the description. No trend discovery, no fact-check, no AI narration unless you want it. Perfect for music releases, product launches, or driving traffic to a website.

```bash
# Promote a SONG: your audio + a cover image + a streaming link
python -m socialbot.cli promo \
  --title "Midnight — out now" \
  --audio ~/music/midnight.mp3 \
  --image ~/art/cover.jpg \
  --link "https://open.spotify.com/track/XXXX" --cta "Stream now" \
  --hashtags "newmusic,indie,spotify" \
  --publish

# Promote a PRODUCT: AI voiceover + your product clip + a shop link
python -m socialbot.cli promo \
  --title "Meet the Model X" \
  --say "The Model X launches today. Ten-hour battery, waterproof, and AI-powered." \
  --video ~/clips/modelx.mp4 \
  --link "https://shop.example.com/model-x" --cta "Shop now" \
  --publish

# Promote a WEBSITE / link: AI voiceover + stock footage + a link
python -m socialbot.cli promo \
  --title "We just launched!" \
  --say "Our new platform is live. Sign up free today." \
  --keywords "startup,technology,website" \
  --link "https://example.com" --cta "Visit the site"
```

How it works:
- **Audio:** `--audio FILE` uses your own track as the soundtrack (trimmed to 60s with a fade-out). Or `--say "TEXT"` generates an AI voiceover instead.
- **Visuals:** `--video FILE` or `--image FILE` (Ken Burns pan/zoom) uses your media. Otherwise `--keywords` pulls stock footage, or you get an animated gradient.
- **Link + CTA:** `--link URL` and `--cta "label"` add a clickable call-to-action line to the description (works on every platform).
- **Length:** `--seconds N` caps it; songs default to `min(track length, 60)`.
- Drop `--publish` to queue it for review instead of posting immediately.

| Flag | Purpose |
|---|---|
| `--title` | On-screen overlay + post title (required) |
| `--audio` | Your audio/song file (soundtrack, no AI voiceover) |
| `--say` | Text for an AI voiceover (use instead of `--audio`) |
| `--image` / `--video` | Your cover/product visual |
| `--keywords` | Stock footage terms if you don't supply your own visual |
| `--link` / `--cta` | Clickable URL + its label in the description |
| `--description` | Post body text (defaults to the title) |
| `--hashtags` | Comma-separated tags |
| `--seconds` | Length cap (default: `min(audio, 60)`) |
| `--publish` | Post now instead of queueing |

### Review queue (for `generate` mode)

```bash
python -m socialbot.cli list                     # see all queued items
python -m socialbot.cli show <id>                # script, fact-check verdict, sources
python -m socialbot.cli open <id>                # play the clip
python -m socialbot.cli approve <id>             # approve for publishing
python -m socialbot.cli reject  <id> --reason "off-brand"
python -m socialbot.cli publish <id> --targets youtube
python -m socialbot.cli publish --all-approved   # publish everything approved
```

### Cost tracking

```bash
python -m socialbot.cli costs            # breakdown by stage + model
python -m socialbot.cli costs --youtube  # YouTube quota usage
python -m socialbot.cli costs --json     # machine-readable JSON
```

---

## Customize your channel

Everything is driven by environment variables — **no code changes required.** There are two ways to set them:

- **Local / manual use:** edit your `.env` file
- **GitHub Actions (cloud schedule):** set [GitHub Variables](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/store-information-in-variables) under *Settings → Secrets and variables → Actions → Variables tab*

### All parameters

| Variable | What it controls | Default |
|---|---|---|
| `CONTENT_NICHE` | Topics Gemini searches for. More specific = better stories. Comma-separated. | AI / space / UFOs |
| `CONTENT_TONE` | The narrator's personality — shapes every word of the script. | Plain-spoken explainer |
| `CLIP_SECONDS` | Target video length in seconds. 15–30s is the Shorts retention sweet spot. | `25` |
| `TTS_VOICES` | Pool of Microsoft neural voices rotated per video. Comma-separated. Run `python -m socialbot.cli voices` to list all 200+ options. | 6 English voices |
| `YOUTUBE_PRIVACY` | `public`, `unlisted`, or `private` | `private` |
| `SPECULATIVE_KEYWORDS` | Topics allowed to auto-publish on a `needs_review` fact-check (inherently unverifiable claims). Comma-separated. | ufo, alien, paranormal, ... |
| `POSTS_PER_RUN` | Clips generated per scheduled run (GitHub Actions). | `2` |

### Preset configurations

Copy any of these into your `.env` (or as GitHub Variables) to change the channel type instantly.

#### Tech / AI — default
```env
CONTENT_NICHE=AI and emerging technology, space and astronomy, UFOs/UAPs and the search for extraterrestrial life
CONTENT_TONE=clear, punchy, plain-spoken explainer; smart and factual but neutral — no hype, no jokes
CLIP_SECONDS=25
SPECULATIVE_KEYWORDS=ufo,ufos,uap,uaps,alien,aliens,extraterrestrial,flying saucer,close encounter,abduction,roswell,paranormal,cryptid
```

#### Business / brand promotion
```env
CONTENT_NICHE=sustainable fashion industry news, eco-friendly consumer trends, ethical manufacturing
CONTENT_TONE=confident brand voice, speaks to eco-conscious consumers, warm but informative — not salesy
CLIP_SECONDS=20
```

#### Finance / investing
```env
CONTENT_NICHE=stock market news, cryptocurrency, personal finance, Federal Reserve decisions, earnings reports
CONTENT_TONE=calm and analytical, data-driven, speaks to retail investors — no hype, no price predictions
CLIP_SECONDS=25
```

#### Fitness / health
```env
CONTENT_NICHE=fitness research, nutrition science, sports medicine, mental health, workout science
CONTENT_TONE=motivating but evidence-based, plain-spoken, practical — no miracle claims
CLIP_SECONDS=20
SPECULATIVE_KEYWORDS=miracle,cure,secret,detox,breakthrough
```

#### Gaming / esports
```env
CONTENT_NICHE=video game releases, esports tournaments, gaming industry news, indie games
CONTENT_TONE=enthusiastic and casual, speaks to gamers, fast-paced — current gaming terms are fine
CLIP_SECONDS=20
```

#### True crime / mysteries
```env
CONTENT_NICHE=unsolved crimes, cold cases, criminal investigations, forensic breakthroughs
CONTENT_TONE=serious and measured, factual, respectful of victims — no speculation
CLIP_SECONDS=30
SPECULATIVE_KEYWORDS=alleged,theory,suspect,conspiracy,unconfirmed
```

#### Local news / community
```env
CONTENT_NICHE=local government, city planning, community events, regional business, neighborhood news
CONTENT_TONE=neutral local news reporter, factual, community-focused, conversational
CLIP_SECONDS=30
```

### Voices

Run `python -m socialbot.cli voices` to list all 200+ available Microsoft neural voices. Add any to `TTS_VOICES` as a comma-separated list. For non-English channels:

```env
# Spanish example
TTS_VOICES=es-MX-JorgeNeural,es-ES-AlvaroNeural,es-US-AlonsoNeural
CONTENT_NICHE=noticias de tecnología, inteligencia artificial, ciencia espacial
CONTENT_TONE=presentador de noticias claro y directo, sin sensacionalismo
```

---

## Automated scheduling (GitHub Actions)

The bot runs on GitHub's servers — **no server, no always-on machine**. Each run spins up, generates and posts clips, then shuts down. The free Actions tier handles 3 runs/day easily (~9 min/day vs the 2,000 min/month free allowance).

### Setup

1. Push this repo to a **private** GitHub repository.
2. Go to *Settings → Secrets and variables → Actions* and configure:

   **Secrets** (encrypted — for API keys and tokens):

   | Secret | Value |
   |---|---|
   | `GEMINI_API_KEY` | Your Gemini API key |
   | `PEXELS_API_KEY` | Your Pexels key (optional) |
   | `YOUTUBE_TOKEN` | Full contents of `secrets/youtube_token.json` after running `youtube-auth` locally |

   **Variables** (plain text — change any time without re-deploying):

   | Variable | Value | If not set |
   |---|---|---|
   | `CONTENT_NICHE` | Your topics | AI / space / UFOs |
   | `CONTENT_TONE` | Narrator style | Plain-spoken explainer |
   | `CLIP_SECONDS` | Length in seconds | `25` |
   | `TTS_VOICES` | Voice pool | 6 English voices |
   | `YOUTUBE_PRIVACY` | `public` / `unlisted` / `private` | `public` |
   | `SPECULATIVE_KEYWORDS` | Topics that bypass strict fact-check | ufo, alien, paranormal, ... |
   | `POSTS_PER_RUN` | Clips per scheduled run | `2` |

3. **Schedule:** the workflow fires at **13:00, 18:00, and 23:00 UTC** by default. Edit the three `cron:` lines in `.github/workflows/auto.yml` for your timezone. [UTC converter →](https://www.timeanddate.com/worldclock/converter.html)

   Common timezone offsets:
   | Timezone | UTC offset | 9am local in cron |
   |---|---|---|
   | US Eastern (ET) | −5 (−4 summer) | `0 14 * * *` |
   | US Central (CT) | −6 (−5 summer) | `0 15 * * *` |
   | US Mountain (MT) | −7 (−6 summer) | `0 16 * * *` |
   | US Pacific (PT) | −8 (−7 summer) | `0 17 * * *` |
   | UK (GMT/BST) | 0 (+1 summer) | `0 9 * * *` |
   | Central Europe (CET) | +1 (+2 summer) | `0 8 * * *` |

4. **Test:** trigger a run from **Actions → auto-post → Run workflow**. Leave the count blank to use your `POSTS_PER_RUN` variable.

### Pausing
Disable the workflow in the Actions tab to pause cloud posting. A local `data/PAUSED` file pauses local runs only and has no effect on the cloud job.

---

## How the pipeline works

```
Trend discovery (or custom topic)
  Gemini + Google Search finds stories trending in the last 24-48 hours,
  filtered to your CONTENT_NICHE. Returns a batch; dedup filter drops
  stories covered in recent runs (data/used_topics.json).

  With --topic: skips this step entirely. Uses your title + facts.
  Saves 1 Flash-Lite API call.

Script + hook
  Gemini writes a ~25s voiceover. The first sentence is the single most
  surprising/concrete fact — the scroll-stopper. Ends on a line that
  loops back into the hook so auto-replays feel seamless.

Fact-check
  A second grounded Gemini call verifies every material claim:
    ok           → publishes automatically
    needs_review → held (unless topic is speculative: UFO/alien/UAP,
                   or was user-provided via --topic)
    rejected     → never published, even for custom topics

Visuals
  Space/astronomy/UAP topics → NASA public-domain imagery + video
  All other topics            → Pexels stock footage
  Both are deduplicated across runs (data/used_broll.json, used_nasa.json)

Assembly (ffmpeg · 1080 × 1920)
  • Hook text burned in big + centered for the first ~2.5s
  • B-roll cuts every ~3s (algorithm rewards fast visual changes)
  • Word-synced captions burned in throughout
  • Voices rotate from the TTS_VOICES pool so clips sound different
```

---

## Responsible use

- **You are responsible** for what gets posted. The fact-check pass reduces risk but is not a guarantee — review the output if accuracy matters.
- Respect YouTube and Facebook automation policies.
- Pexels footage is licensed for free commercial use. NASA imagery is public domain.
- edge-tts uses Microsoft Edge's Read Aloud feature — suitable for personal and small-scale automated use.
