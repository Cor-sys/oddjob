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

### Free tier limits (verified from AI Studio rate-limit dashboard)

> Always check your own project at [aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) — limits vary by project and Google adjusts them without notice.

| Service | Free limit | Notes |
|---|---|---|
| **Gemini 2.5 Flash** | 5 RPM · **20 RPD** | Script writing |
| **Gemini 2.5 Flash-Lite** | 10 RPM · **20 RPD** | Trends + fact-check |
| **Gemini search grounding** | **500 RPD** (separate quota) | The live-web search tool has its own more generous counter |
| **Pexels** (b-roll footage) | 200 req/hr · 20,000 req/month | More than enough |
| **NASA** (space footage, keyless) | 30 req/hr · 50 req/day | Register a free key for 1,000 req/hr |
| **YouTube Data API v3** | 10,000 quota units/day · ~100 units/upload | ~100 uploads/day free |
| **edge-tts** (voiceover) | Unlimited — no key needed | Free Microsoft neural voices |
| **GitHub Actions** | 2,000 min/month (free accounts) | Each run takes ~3 min |

### The math

Each video uses **3 calls**: 1 Flash (script) + 2 Flash-Lite (trends + fact-check). The bot generates **2 per run × 3 runs = 6 per day** as a buffer — the fact-check gate typically holds 1–2 per day (rejected or unverifiable claims), so you reliably land **~4 published videos/day**.

| Scenario | Flash RPD used | Flash-Lite RPD used | Free headroom left |
|---|---|---|---|
| **Default: 3 runs × 2 clips** | **6 / 20 (30%)** | **9 / 20 (45%)** | Flash 14 · Lite 11 |
| 3 runs × 1 clip (conservative) | 3 / 20 (15%) | 6 / 20 (30%) | Flash 17 · Lite 14 |
| + 1 manual test run on top | +1 Flash +2 Lite | +1 Flash +2 Lite | deducted from above |
| **Free ceiling** | ~20 clips/day | ~10 clips/day | 0 |

**The default 2-per-run setup uses 30–45% of the free RPD**, leaving enough headroom for an occasional manual test run. Avoid running multiple manual tests on the same day as your scheduled runs.

With billing (Tier 1): Flash gets ~10,000 RPD, Flash-Lite gets **Unlimited**.

With billing (Tier 1): Flash gets ~10,000 RPD, Flash-Lite gets **Unlimited**.

### What it costs on a billed account

Token usage is the only charge — everything else stays $0:

| Per video | Approx. cost |
|---|---|
| Trend discovery (flash-lite, grounded) | < $0.01 |
| Script writing (flash) | < $0.01 |
| Fact-check (flash-lite, grounded) | < $0.01 |
| **Total per video** | **~$0.01 – $0.02** |
| Voiceover, footage, YouTube posting | **$0.00** (always free) |

**At 3 videos/day that is roughly $1–2/month.** YouTube's Data API, Pexels, edge-tts, and NASA remain free regardless of volume.

> The bot logs every API call to `data/costs.jsonl`. Run `python -m socialbot.cli costs` to see a live breakdown by stage and model.

---

## Customize for your use case

The bot is fully configurable — niche, tone, length, voices, posting frequency, platforms. **No code changes required.** Everything is driven by environment variables.

There are two ways to set them:
- **Local / manual use:** edit your `.env` file
- **GitHub Actions (cloud schedule):** set [GitHub Variables](https://docs.github.com/en/actions/writing-workflows/choosing-what-your-workflow-does/store-information-in-variables) under *Settings → Secrets and variables → Actions → Variables tab* — the workflow picks them up automatically with no file editing

---

### All customizable parameters

| Variable | What it controls | Default | Notes |
|---|---|---|---|
| `CONTENT_NICHE` | Topics Gemini searches for trends. Be specific — the more concrete, the better the stories. | AI / space / UFOs | Comma-separated list |
| `CONTENT_TONE` | The narrator's personality. Shapes every word of the script. | Plain-spoken explainer | Describe it like you'd describe a TV presenter |
| `CLIP_SECONDS` | Target video length in seconds. | `25` | 15–30s is the Shorts retention sweet spot |
| `TTS_VOICES` | Pool of Microsoft neural voices rotated per video. | 6 English voices | Run `python -m socialbot.cli voices` to list all 200+ options |
| `YOUTUBE_PRIVACY` | `public`, `unlisted`, or `private` | `private` | Set to `private` while testing, `public` when live |
| `SPECULATIVE_KEYWORDS` | Topics allowed to post on a `needs_review` fact-check result (inherently unverifiable claims). | ufo,alien,paranormal,... | Comma-separated, no spaces around commas |
| `POSTS_PER_RUN` | How many clips to generate per scheduled run. | `2` | 3 runs × 2 = 6 generated, ~4 published after filtering |

**Posting schedule** is set in `.github/workflows/auto.yml`. The three `cron:` lines are in UTC — change them to match your timezone. For example, to post at 9am, 2pm, and 7pm US Eastern (UTC-4 in summer):
```yaml
- cron: "0 13 * * *"   # 9am ET
- cron: "0 18 * * *"   # 2pm ET
- cron: "0 23 * * *"   # 7pm ET
```
[UTC timezone converter →](https://www.timeanddate.com/worldclock/converter.html)

---

### Preset configurations

Copy-paste any of these into your `.env` (or as GitHub Variables) to change the channel type instantly.

#### Business / brand promotion
The bot sources industry news and angles it toward your brand. Great for product launches, service businesses, or thought leadership.
```env
CONTENT_NICHE=sustainable fashion brand, eco-friendly clothing, ethical manufacturing trends
CONTENT_TONE=confident brand voice, speaks to eco-conscious consumers, warm but informative — not salesy
CLIP_SECONDS=20
```

#### Local news / community
```env
CONTENT_NICHE=local government, city planning, community events, regional business, neighborhood stories
CONTENT_TONE=neutral local news reporter, factual, community-focused, no sensationalism
CLIP_SECONDS=30
```

#### Finance / investing
```env
CONTENT_NICHE=stock market, cryptocurrency, personal finance tips, economic policy, Federal Reserve, earnings reports
CONTENT_TONE=calm and analytical, data-driven, speaks to retail investors, avoids hype and predictions
CLIP_SECONDS=25
```

#### Fitness / health
```env
CONTENT_NICHE=fitness research, nutrition science, sports medicine, workout trends, mental health
CONTENT_TONE=motivating but evidence-based, plain-spoken, practical — no bro-science or miracle claims
CLIP_SECONDS=20
SPECULATIVE_KEYWORDS=miracle,cure,secret,detox
```

#### Gaming / esports
```env
CONTENT_NICHE=video game releases, esports tournaments, gaming industry news, indie games, game dev
CONTENT_TONE=enthusiastic and casual, speaks to gamers, fast-paced — current gaming slang is fine
CLIP_SECONDS=20
```

#### True crime / mysteries
```env
CONTENT_NICHE=unsolved crimes, cold cases, criminal investigations, forensic science breakthroughs
CONTENT_TONE=serious and measured, factual, respectful of victims — no sensationalism or speculation
CLIP_SECONDS=30
SPECULATIVE_KEYWORDS=suspect,alleged,theory,conspiracy
```

#### Tech / AI (the default)
```env
CONTENT_NICHE=AI and emerging technology, space and astronomy, UFOs/UAPs and the search for extraterrestrial life
CONTENT_TONE=clear, punchy, plain-spoken explainer; smart and factual but neutral — no hype, no jokes
CLIP_SECONDS=25
SPECULATIVE_KEYWORDS=ufo,ufos,uap,uaps,alien,aliens,extraterrestrial,flying saucer,close encounter,abduction,roswell,paranormal,cryptid
```

---

### Voices

The bot ships with 6 English voices that rotate per video. To customize, run:
```bash
python -m socialbot.cli voices
```
This lists all 200+ available Microsoft neural voices by language and name. Add any you like to `TTS_VOICES` as a comma-separated list. For a non-English channel, use voices matching your language:
```env
# Spanish channel
TTS_VOICES=es-MX-JorgeNeural,es-ES-AlvaroNeural,es-US-AlonsoNeural
CONTENT_NICHE=noticias de tecnología, inteligencia artificial, ciencia espacial
CONTENT_TONE=presentador de noticias claro y directo, sin sensacionalismo
```

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
2. Go to *Settings → Secrets and variables → Actions* and add:

   **Secrets tab** (sensitive values — encrypted):
   | Secret | Value |
   |---|---|
   | `GEMINI_API_KEY` | Your Gemini API key |
   | `PEXELS_API_KEY` | Your Pexels key (optional) |
   | `YOUTUBE_TOKEN` | Contents of `secrets/youtube_token.json` after running `youtube-auth` locally |

   **Variables tab** (non-sensitive config — edit any time without re-deploying):
   | Variable | Value | Default if not set |
   |---|---|---|
   | `CONTENT_NICHE` | Your topics (see presets above) | AI / space / UFOs |
   | `CONTENT_TONE` | Narrator style | Plain-spoken explainer |
   | `CLIP_SECONDS` | Video length in seconds | `25` |
   | `TTS_VOICES` | Comma-separated voice pool | 6 English voices |
   | `YOUTUBE_PRIVACY` | `public` / `unlisted` / `private` | `public` |
   | `SPECULATIVE_KEYWORDS` | Topics allowed to post unverified | ufo,alien,paranormal,... |
   | `POSTS_PER_RUN` | Clips generated per scheduled run | `2` |

   > Variables let you change your niche, tone, or posting volume directly in the GitHub UI — no file editing or re-deploying required.

3. The workflow fires at **13:00, 18:00, and 23:00 UTC** by default. To change the schedule, edit the three `cron:` lines in `.github/workflows/auto.yml`.
4. Test with a manual trigger: **Actions → auto-post → Run workflow**.

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
