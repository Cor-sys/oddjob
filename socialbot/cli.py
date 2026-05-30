"""Command-line interface for Oddjob.

Examples:
  python -m socialbot.cli voices
  python -m socialbot.cli trends --count 5 --niche "space exploration"
  python -m socialbot.cli generate --count 3
  python -m socialbot.cli list
  python -m socialbot.cli show <id>
  python -m socialbot.cli open <id>
  python -m socialbot.cli approve <id>
  python -m socialbot.cli reject <id> --reason "boring"
  python -m socialbot.cli publish <id> --targets youtube,facebook
  python -m socialbot.cli publish --all-approved
  python -m socialbot.cli costs
  python -m socialbot.cli costs --youtube
"""
from __future__ import annotations

import argparse
import os
import sys


def _cmd_voices(args) -> int:
    from .media.tts import list_voices

    for v in list_voices(args.lang):
        print(v)
    return 0


def _cmd_trends(args) -> int:
    from .trends import discover

    topics = discover(count=args.count, niche=args.niche)
    for i, t in enumerate(topics, 1):
        print(f"\n[{i}] {t.title}")
        print(f"    {t.summary}")
        if t.why_trending:
            print(f"    why: {t.why_trending}")
        if t.keywords:
            print(f"    keywords: {', '.join(t.keywords)}")
    return 0


def _cmd_generate(args) -> int:
    from .pipeline import generate, generate_from_topic
    from .trends import Topic

    if args.topic:
        topic = Topic(
            title=args.topic,
            summary=args.facts or args.topic,
            why_trending="user-provided",
            keywords=[k.strip() for k in (args.keywords or "").split(",") if k.strip()],
        )
        items = [generate_from_topic(topic, seconds=args.seconds)]
    else:
        items = generate(count=args.count, niche=args.niche, seconds=args.seconds)
    print(f"\nGenerated {len(items)} item(s). Review with: python -m socialbot.cli list")
    return 0


def _cmd_promo(args) -> int:
    from .pipeline import make_promo, publish_promo

    kws = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    tags = [h.strip() for h in (args.hashtags or "").split(",") if h.strip()]
    item = make_promo(
        title=args.title, audio=args.audio, say=args.say, image=args.image,
        video=args.video, keywords=kws, link=args.link, cta=args.cta,
        description=args.description, hashtags=tags, seconds=args.seconds,
    )
    if args.publish:
        targets = tuple(t.strip() for t in args.targets.split(",") if t.strip())
        results = publish_promo(item, targets=targets)
        for plat, r in results.items():
            print(f"  {plat} -> {r.get('url') or r.get('error')}")
    else:
        print(f"\nQueued {item.id}. Preview: python -m socialbot.cli open {item.id}")
        print(f"Publish when ready: python -m socialbot.cli publish {item.id} --targets youtube")
    return 0


def _status_tag(item) -> str:
    fc = item.meta.get("factcheck", {}).get("verdict", "?")
    return f"{item.status:10s} fc={fc:13s}"


def _cmd_list(args) -> int:
    from . import review

    items = review.list_items(status=args.status)
    items += review.list_items(status=args.status, base=review.PUBLISHED_DIR)
    if not items:
        print("Queue is empty.")
        return 0
    for it in items:
        title = it.meta.get("on_screen_title", it.meta.get("topic_title", ""))
        dur = it.meta.get("duration", "?")
        print(f"{_status_tag(it)}  {it.id}  ({dur}s)  {title}")
    return 0


def _cmd_show(args) -> int:
    from . import review

    item = review.get(args.id)
    if not item:
        print(f"Not found: {args.id}", file=sys.stderr)
        return 1
    m = item.meta
    print(f"id:        {item.id}")
    print(f"status:    {item.status}")
    print(f"title:     {m.get('on_screen_title')}")
    print(f"topic:     {m.get('topic_title')}")
    print(f"duration:  {m.get('duration', '?')}s   clip: {item.clip_path}")
    fc = m.get("factcheck", {})
    print(f"\nfact-check: {fc.get('verdict')} — {fc.get('summary')}")
    for c in fc.get("claims", []):
        print(f"   [{c.get('status')}] {c.get('claim')}  {c.get('note')}")
    print("\nnarration:")
    print(f"   {m.get('script', {}).get('narration', '')}")
    print("\nsources:")
    for s in m.get("topic", {}).get("sources", [])[:8]:
        print(f"   {s}")
    return 0


def _cmd_open(args) -> int:
    from . import review

    item = review.get(args.id)
    if not item or not item.clip_path:
        print(f"No clip for {args.id}", file=sys.stderr)
        return 1
    if sys.platform == "win32":
        os.startfile(item.clip_path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        os.system(f'open "{item.clip_path}"')
    else:
        os.system(f'xdg-open "{item.clip_path}"')
    return 0


def _cmd_approve(args) -> int:
    from . import review

    item = review.approve(args.id)
    print(f"Approved {item.id}")
    return 0


def _cmd_reject(args) -> int:
    from . import review

    item = review.reject(args.id, reason=args.reason)
    print(f"Rejected {item.id}")
    return 0


def _cmd_youtube_auth(args) -> int:
    from .config import ROOT, settings
    from .publish.youtube import _credentials

    _credentials()  # opens a browser to authorize if no valid token exists
    token_path = ROOT / settings.youtube_token_file
    print(f"YouTube authorized. Token saved to: {token_path}")
    print("Next: copy that file's contents into a GitHub Secret named YOUTUBE_TOKEN.")
    return 0


def _cmd_auto(args) -> int:
    from .config import DATA_DIR
    from .pipeline import auto_run, auto_run_custom_topic
    from .trends import Topic

    pause_file = DATA_DIR / "PAUSED"
    if pause_file.exists():
        print(f"auto is PAUSED — delete {pause_file} to resume.")
        return 0
    targets = tuple(t.strip() for t in args.targets.split(",") if t.strip())
    if args.topic:
        topic = Topic(
            title=args.topic,
            summary=args.facts or args.topic,
            why_trending="user-provided",
            keywords=[k.strip() for k in (args.keywords or "").split(",") if k.strip()],
        )
        auto_run_custom_topic(topic, targets=targets)
    else:
        auto_run(count=args.count, targets=targets, niche=args.niche)
    return 0


def _cmd_costs(args) -> int:
    import json as _json

    from . import costs

    s = costs.summary()
    if args.json:
        print(_json.dumps(s, indent=2))
        return 0

    yt = s["youtube"]
    if args.youtube:
        print("YouTube posting")
        print("─" * 32)
        print(f"  videos uploaded:   {yt['uploads']}")
        print(f"  quota units used:  {yt['quota_units_used']:,} (free tier: 10,000/day)")
        print(f"  money spent:       ${yt['posting_cost_usd']:.2f}  (Data API is free)")
        return 0

    print("Spend summary (estimated, Gemini list prices)")
    print("─" * 46)
    print(f"  total estimated cost:  ${s['total_estimated_cost_usd']:.4f}")
    print(f"  tokens in / out:       {s['total_input_tokens']:,} / {s['total_output_tokens']:,}")
    print(f"  Gemini calls:          {s['llm_calls']}")
    if s["by_stage"]:
        print("\n  by stage:")
        for stage, cost in s["by_stage"].items():
            print(f"    {stage:12s} ${cost:.4f}")
    if s["by_model"]:
        print("\n  by model:")
        for model, m in s["by_model"].items():
            print(f"    {model:26s} ${m['cost_usd']:.4f}  ({m['calls']} calls)")
    print("\n  YouTube posting:")
    print(f"    {yt['uploads']} upload(s), {yt['quota_units_used']:,} quota units, "
          f"${yt['posting_cost_usd']:.2f} spent (free API)")
    return 0


def _cmd_serve(args) -> int:
    from .web.app import serve

    print(f"Review dashboard at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    serve(host=args.host, port=args.port)
    return 0


def _cmd_publish(args) -> int:
    from . import review
    from .publish import publish_item

    targets = tuple(t.strip() for t in args.targets.split(",") if t.strip())

    if args.all_approved:
        items = review.list_items(status=review.APPROVED)
        if not items:
            print("No approved items to publish.")
            return 0
    else:
        if not args.id:
            print("Provide an <id> or --all-approved", file=sys.stderr)
            return 2
        item = review.get(args.id)
        if not item:
            print(f"Not found: {args.id}", file=sys.stderr)
            return 1
        items = [item]

    for it in items:
        print(f"Publishing {it.id}...")
        publish_item(it, targets=targets)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="socialbot", description="Trend -> clip -> review -> publish")
    sub = p.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("voices", help="list available TTS voices")
    v.add_argument("--lang", default="en")
    v.set_defaults(func=_cmd_voices)

    t = sub.add_parser("trends", help="discover trending topics (no video)")
    t.add_argument("--count", type=int, default=5)
    t.add_argument("--niche", default=None)
    t.set_defaults(func=_cmd_trends)

    g = sub.add_parser("generate", help="discover + script + vet + render to review queue")
    g.add_argument("--count", type=int, default=3)
    g.add_argument("--niche", default=None)
    g.add_argument("--seconds", type=int, default=None)
    g.add_argument("--topic", default=None, metavar="TITLE",
                   help="skip trend discovery and use this custom title instead")
    g.add_argument("--facts", default=None, metavar="TEXT",
                   help="key facts / description for the custom topic")
    g.add_argument("--keywords", default=None, metavar="KW1,KW2",
                   help="comma-separated b-roll search keywords for custom topic")
    g.set_defaults(func=_cmd_generate)

    pr = sub.add_parser("promo", help="PROMO: build a post from your own content (song/product/link)")
    pr.add_argument("--title", required=True, help="overlay text + post title")
    pr.add_argument("--audio", default=None, help="your audio file (e.g. a song) — used as the soundtrack")
    pr.add_argument("--say", default=None, help="text for an AI voiceover (use instead of --audio)")
    pr.add_argument("--image", default=None, help="your cover/product image (Ken Burns pan/zoom)")
    pr.add_argument("--video", default=None, help="your background video clip")
    pr.add_argument("--keywords", default=None, metavar="KW1,KW2",
                    help="stock-footage search terms if no --image/--video given")
    pr.add_argument("--link", default=None, help="URL for the description (song/product/website)")
    pr.add_argument("--cta", default=None, help="call-to-action label for the link, e.g. 'Stream now'")
    pr.add_argument("--description", default=None, help="post description (defaults to the title)")
    pr.add_argument("--hashtags", default=None, metavar="TAG1,TAG2", help="comma-separated hashtags")
    pr.add_argument("--seconds", type=int, default=None, help="length cap (default: min(audio, 60))")
    pr.add_argument("--targets", default="youtube")
    pr.add_argument("--publish", action="store_true", help="publish now instead of queueing for review")
    pr.set_defaults(func=_cmd_promo)

    ls = sub.add_parser("list", help="list review queue")
    ls.add_argument("--status", default=None)
    ls.set_defaults(func=_cmd_list)

    sh = sub.add_parser("show", help="show one item's details")
    sh.add_argument("id")
    sh.set_defaults(func=_cmd_show)

    op = sub.add_parser("open", help="open an item's clip in the default player")
    op.add_argument("id")
    op.set_defaults(func=_cmd_open)

    ap = sub.add_parser("approve", help="approve an item for publishing")
    ap.add_argument("id")
    ap.set_defaults(func=_cmd_approve)

    rj = sub.add_parser("reject", help="reject an item")
    rj.add_argument("id")
    rj.add_argument("--reason", default="")
    rj.set_defaults(func=_cmd_reject)

    ya = sub.add_parser("youtube-auth", help="one-time: authorize YouTube in your browser, save token")
    ya.set_defaults(func=_cmd_youtube_auth)

    au = sub.add_parser("auto", help="FULLY AUTOMATED: generate + publish fact-check-ok clips, no review")
    au.add_argument("--count", type=int, default=3)
    au.add_argument("--niche", default=None)
    au.add_argument("--targets", default="youtube,facebook")
    au.add_argument("--topic", default=None, metavar="TITLE",
                   help="use a custom topic instead of trend discovery")
    au.add_argument("--facts", default=None, metavar="TEXT",
                   help="key facts / description for the custom topic")
    au.add_argument("--keywords", default=None, metavar="KW1,KW2",
                   help="comma-separated b-roll keywords for custom topic")
    au.set_defaults(func=_cmd_auto)

    co = sub.add_parser("costs", help="show estimated spend (Gemini) + YouTube posting usage")
    co.add_argument("--youtube", action="store_true", help="show only the YouTube-posting view")
    co.add_argument("--json", action="store_true", help="emit the full summary as JSON")
    co.set_defaults(func=_cmd_costs)

    sv = sub.add_parser("serve", help="launch the web review dashboard")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.set_defaults(func=_cmd_serve)

    pb = sub.add_parser("publish", help="publish an approved item (or all approved)")
    pb.add_argument("id", nargs="?", default=None)
    pb.add_argument("--targets", default="youtube,facebook")
    pb.add_argument("--all-approved", action="store_true")
    pb.set_defaults(func=_cmd_publish)

    return p


def _force_utf8_stdout() -> None:
    """Windows consoles default to cp1252, which can't encode the Unicode the bot
    prints (box-drawing rules, en-dashes in the tone string, etc.) and raises
    UnicodeEncodeError. Switch stdout/stderr to UTF-8 where supported."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyError as e:
        print(f"not found: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
