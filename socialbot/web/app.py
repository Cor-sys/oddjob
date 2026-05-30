"""Flask review dashboard: watch, approve/reject, and publish clips from a browser.

Run with:  python -m socialbot.cli serve
Then open http://127.0.0.1:8000  (or expose via `cloudflared tunnel`).
"""
from __future__ import annotations

import threading

from flask import (Flask, abort, flash, redirect, render_template_string,
                   request, send_file, url_for)

from .. import review
from ..config import PUBLISHED_DIR
from ..publish import publish_item

app = Flask(__name__)
app.secret_key = "socialbot-review-dashboard"

# simple shared state for the background "generate" job
_gen = {"running": False, "msg": ""}

_PAGE = """
<!doctype html><html><head><meta charset="utf-8">
<title>Oddjob — Review</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; background:#0f1115; color:#e6e6e6; margin:0; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  .sub { color:#8a8f98; font-size:13px; margin-bottom:20px; }
  .bar { display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:20px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:18px; }
  .card { background:#171a21; border:1px solid #242833; border-radius:12px; overflow:hidden; display:flex; flex-direction:column; }
  video { width:100%; background:#000; aspect-ratio:9/16; object-fit:contain; }
  .body { padding:12px 14px; display:flex; flex-direction:column; gap:8px; }
  .title { font-weight:600; font-size:15px; }
  .meta { font-size:12px; color:#9aa0aa; }
  .badge { display:inline-block; font-size:11px; padding:2px 8px; border-radius:999px; font-weight:600; }
  .pending{background:#3a3320;color:#f5c451;} .approved{background:#1f3a24;color:#6ee787;}
  .rejected{background:#3a1f1f;color:#ff7b7b;} .published{background:#1f2a3a;color:#6ea8fe;}
  .ok{color:#6ee787;} .needs_review{color:#f5c451;} .rejected_fc{color:#ff7b7b;}
  .acts { display:flex; gap:6px; flex-wrap:wrap; margin-top:4px; }
  button { cursor:pointer; border:0; border-radius:8px; padding:7px 11px; font-size:13px; font-weight:600; color:#fff; }
  .b-approve{background:#2f9e44;} .b-reject{background:#c92a2a;} .b-publish{background:#1c7ed6;}
  .b-gen{background:#7048e8;} .ghost{background:#242833;color:#cdd2da;}
  a { color:#6ea8fe; text-decoration:none; }
  .flash { background:#1f2a3a; border:1px solid #2c4a73; padding:10px 14px; border-radius:8px; margin-bottom:16px; font-size:13px; }
  .section { margin:26px 0 10px; font-size:14px; color:#8a8f98; text-transform:uppercase; letter-spacing:.05em; }
  .empty { color:#6b7280; font-size:14px; }
</style></head><body>
<h1>Oddjob — Review queue</h1>
<div class="sub">Nothing posts without your approval.</div>

{% with msgs = get_flashed_messages() %}{% for m in msgs %}<div class="flash">{{ m }}</div>{% endfor %}{% endwith %}
{% if gen.msg %}<div class="flash">{{ gen.msg }}{% if gen.running %} (running…){% endif %}</div>{% endif %}

<div class="bar">
  <form method="post" action="{{ url_for('generate') }}" style="display:flex;gap:6px;align-items:center;">
    <input type="number" name="count" value="2" min="1" max="5" style="width:54px;padding:6px;border-radius:8px;border:1px solid #242833;background:#0f1115;color:#e6e6e6;">
    <button class="b-gen" {{ 'disabled' if gen.running else '' }}>Generate now</button>
  </form>
  <a href="{{ url_for('index') }}"><button class="ghost">↻ Refresh</button></a>
</div>

<div class="section">Pending review ({{ pending|length }})</div>
{% if not pending %}<div class="empty">Queue is empty — hit “Generate now”.</div>{% endif %}
<div class="grid">
{% for it in pending %}
  <div class="card">
    {% if it.clip_path %}<video controls preload="metadata" src="{{ url_for('media', item_id=it.id) }}"></video>{% endif %}
    <div class="body">
      <span class="badge {{ it.status }}">{{ it.status }}</span>
      <div class="title">{{ it.meta.get('on_screen_title') or it.meta.get('topic_title') }}</div>
      <div class="meta">{{ it.meta.get('duration','?') }}s · {{ it.meta.get('voice','') }}</div>
      <div class="meta">fact-check: <span class="{{ 'ok' if it.meta.get('factcheck',{}).get('verdict')=='ok' else 'needs_review' }}">{{ it.meta.get('factcheck',{}).get('verdict','?') }}</span></div>
      <div class="acts">
        {% if it.status != 'approved' %}<form method="post" action="{{ url_for('approve', item_id=it.id) }}"><button class="b-approve">Approve</button></form>{% endif %}
        <form method="post" action="{{ url_for('reject', item_id=it.id) }}"><button class="b-reject">Reject</button></form>
        <form method="post" action="{{ url_for('publish', item_id=it.id) }}"><button class="b-publish">Publish</button></form>
      </div>
    </div>
  </div>
{% endfor %}
</div>

<div class="section">Published ({{ published|length }})</div>
{% if not published %}<div class="empty">None yet.</div>{% endif %}
<div class="grid">
{% for it in published %}
  <div class="card">
    {% if it.clip_path %}<video controls preload="metadata" src="{{ url_for('media', item_id=it.id) }}"></video>{% endif %}
    <div class="body">
      <span class="badge published">published</span>
      <div class="title">{{ it.meta.get('on_screen_title') or it.meta.get('topic_title') }}</div>
    </div>
  </div>
{% endfor %}
</div>
</body></html>
"""


@app.route("/")
def index():
    return render_template_string(
        _PAGE,
        pending=review.list_items(),
        published=review.list_items(base=PUBLISHED_DIR),
        gen=_gen,
    )


@app.route("/media/<item_id>")
def media(item_id):
    item = review.get(item_id)
    if not item or not item.clip_path:
        abort(404)
    return send_file(item.clip_path, mimetype="video/mp4", conditional=True)


@app.route("/approve/<item_id>", methods=["POST"])
def approve(item_id):
    review.approve(item_id)
    return redirect(url_for("index"))


@app.route("/reject/<item_id>", methods=["POST"])
def reject(item_id):
    review.reject(item_id, reason=request.form.get("reason", ""))
    return redirect(url_for("index"))


@app.route("/publish/<item_id>", methods=["POST"])
def publish(item_id):
    item = review.get(item_id)
    if not item:
        abort(404)
    if item.status == review.PENDING:
        item = review.approve(item_id)
    targets = tuple(request.form.getlist("targets")) or ("youtube", "facebook")
    try:
        results = publish_item(item, targets=targets)
        flash(f"Published {item.id}: {results}")
    except Exception as e:
        flash(f"Publish failed for {item.id}: {e}")
    return redirect(url_for("index"))


@app.route("/generate", methods=["POST"])
def generate():
    count = max(1, min(5, int(request.form.get("count", 2))))

    def _run():
        _gen.update(running=True, msg=f"Generating {count} clip(s)…")
        try:
            from ..pipeline import generate as run_generate
            items = run_generate(count=count)
            _gen["msg"] = f"Generated {len(items)} clip(s). Refresh to see them."
        except Exception as e:
            _gen["msg"] = f"Generation error: {e}"
        finally:
            _gen["running"] = False

    if not _gen["running"]:
        threading.Thread(target=_run, daemon=True).start()
    return redirect(url_for("index"))


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    app.run(host=host, port=port, debug=False)
