#!/usr/bin/env python3
"""
dogtrend.py - Give it a topic. It finds Instagram reels, reads them
(screenshots + transcript + on-screen text), finds what works, and
writes a weekly report with video ideas.

Services used:
  SocialCrawl  -> finds the reels (keyword search) and their numbers
  FrameFetch   -> screenshots, transcript, on-screen text for each reel
  Gemini       -> reads everything and writes the report

Usage:
  python dogtrend.py check
  python dogtrend.py run "dog longevity"
  python dogtrend.py report            (rebuild report from saved data)
Add --debug to save raw service replies in data/debug/ (keys are never saved).
"""
import argparse
import base64
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_PATH)
    load_dotenv()
except ImportError:
    pass

DATA = ROOT / "data"
REPORTS = ROOT / "reports"
DEBUG_DIR = DATA / "debug"
DB_PATH = DATA / "dogtrend.db"
DEBUG = False


# ----------------------------------------------------------------- helpers
def env(name, default=None, required=False):
    v = os.getenv(name, default)
    if isinstance(v, str):
        v = v.split("#")[0].strip()  # allow inline comments in .env
    if required and not v:
        sys.exit(f"Missing {name}. Open your .env file and add it.")
    return v


def envnum(name, default):
    try:
        return float(env(name, str(default)))
    except ValueError:
        return float(default)


def dig(obj, path, default=None):
    """Read a.b.c from nested dicts, safely."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


def first(obj, paths, default=None):
    for p in paths:
        v = dig(obj, p)
        if v not in (None, "", []):
            return v
    return default


def save_debug(name, payload):
    if not DEBUG:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / f"{int(time.time())}_{name}.json").write_text(
        json.dumps(payload, indent=2, default=str)[:200000], encoding="utf-8"
    )


def parse_json_loose(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", text, flags=re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def request_with_retry(method, url, retry_busy=True, **kw):
    timeout = kw.pop("timeout", 60)
    for attempt in range(4):
        try:
            r = requests.request(method, url, timeout=timeout, **kw)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < 3:
                time.sleep(2)
                continue
            raise
        if r.status_code in (429, 503):
            if not retry_busy:
                return r  # Return immediately so caller can failover instantly
            if attempt < 3:
                time.sleep(2)
                continue
        return r
    return r


# ---------------------------------------------------------------- database
def db():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    con.execute(
        """CREATE TABLE IF NOT EXISTS reels(
            url TEXT PRIMARY KEY, topic TEXT, found_at TEXT, handle TEXT,
            views INTEGER, likes INTEGER, comments INTEGER, caption TEXT,
            published_at TEXT, duration REAL, thumb TEXT,
            transcript TEXT, overlay TEXT, frame_count INTEGER,
            analysis TEXT, score REAL, status TEXT DEFAULT 'found', error TEXT)"""
    )
    return con


# -------------------------------------------------------------- SocialCrawl
SC_BASE = "https://www.socialcrawl.dev/v1"


def sc_get(path, params):
    key = env("SOCIALCRAWL_API_KEY", required=True)
    r = request_with_retry("GET", SC_BASE + path, params=params, headers={"x-api-key": key})
    try:
        j = r.json()
    except ValueError:
        raise RuntimeError(f"SocialCrawl sent a reply I could not read (HTTP {r.status_code}).")
    save_debug("socialcrawl_" + path.strip("/").replace("/", "_"), j)
    if r.status_code >= 400 or j.get("success") is False:
        err = j.get("error", {}) if isinstance(j, dict) else {}
        raise RuntimeError(f"SocialCrawl error {r.status_code}: {err.get('message') or err.get('type') or 'unknown'}")
    return j


def normalize_row(row):
    p = row.get("post") if isinstance(row.get("post"), dict) else row
    author = row.get("author") or p.get("author") or {}
    url = first(p, ["url", "permalink", "content.url"]) or first(row, ["url", "post.url"])
    if not url or "instagram.com" not in url:
        return None
    return {
        "url": url.split("?")[0],
        "handle": first(author, ["username", "handle"]) if isinstance(author, dict) else None,
        "views": int(first(p, ["engagement.views", "views", "play_count"], 0) or 0),
        "likes": int(first(p, ["engagement.likes", "likes", "like_count"], 0) or 0),
        "comments": int(first(p, ["engagement.comments", "comments", "comment_count"], 0) or 0),
        "caption": first(p, ["content.caption", "content.text", "caption", "text"], "") or "",
        "published_at": first(p, ["published_at", "created_at", "timestamp"]),
        "duration": first(p, ["content.duration_seconds", "duration_seconds", "duration"]),
        "thumb": first(p, ["content.thumbnail_url", "thumbnail_url", "thumbnail"]),
    }


def discover(con, topic, phrases, limit=5):
    pages_budget = int(envnum("SC_MAX_CREDITS", 15))
    search_phrases = phrases[:2] if len(phrases) > 2 else phrases
    per_phrase = max(1, pages_budget // max(1, len(search_phrases)))
    total_new = 0
    for ph in search_phrases:
        print(f"  searching reels: {ph!r}")
        params = {
            "query": ph,
            "date_posted": env("DATE_WINDOW", "last-month"),
            "relevance": "filter",
            "sort_rows": "views",
            "max_pages": per_phrase,
            "max_credits": per_phrase,
        }
        min_v = int(envnum("MIN_VIEWS", 0))
        try:
            j = sc_get("/instagram/search/reels", params)
        except RuntimeError as e:
            print(f"    skipped: {e}")
            continue
        items = first(j, ["data.items", "data.results", "data.posts", "data"], []) or []
        if not isinstance(items, list):
            items = []
        for row in items:
            if not isinstance(row, dict):
                continue
            n = normalize_row(row)
            if not n:
                continue
            if min_v > 0 and n.get("views") and n["views"] < min_v:
                continue
            cur = con.execute("SELECT 1 FROM reels WHERE url=?", (n["url"],)).fetchone()
            if cur:
                continue
            con.execute(
                """INSERT OR IGNORE INTO reels(url,topic,found_at,handle,views,likes,comments,caption,
                   published_at,duration,thumb) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (n["url"], topic, datetime.now().isoformat(), n["handle"], n["views"], n["likes"],
                 n["comments"], n["caption"], n["published_at"], n["duration"], n["thumb"]),
            )
            total_new += 1
        con.commit()
        if total_new >= max(8, (limit or 5) * 2):
            break
    print(f"  found {total_new} new reels")
    return total_new


# ---------------------------------------------------------------- FrameFetch
FF_BASE = env("FRAMEFETCH_BASE_URL", "https://framefetch.net")
_ff_style = None


def ff_post(path, payload):
    """Send a FrameFetch request. Tries the common key-header styles once."""
    global _ff_style
    key = env("FRAMEFETCH_API_KEY", required=True)
    styles = {
        "bearer": {"Authorization": f"Bearer {key}"},
        "raw": {"Authorization": key},
        "xkey": {"x-api-key": key},
    }
    order = [_ff_style] if _ff_style else ["bearer", "raw", "xkey"]
    last = None
    for s in order:
        r = request_with_retry("POST", FF_BASE + path, json=payload,
                               headers={**styles[s], "Content-Type": "application/json"}, timeout=35)
        if r.status_code in (401, 403):
            last = r
            continue
        _ff_style = s
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"FrameFetch sent a reply I could not read (HTTP {r.status_code}).")
        save_debug("framefetch_" + path.strip("/").replace("/", "_"), j)
        if r.status_code == 402:
            raise RuntimeError("FrameFetch says you are out of credit. Check your account at framefetch.net.")
        if r.status_code >= 400:
            msg = j.get("error") if isinstance(j, dict) else j
            raise RuntimeError(f"FrameFetch error {r.status_code}: {str(msg)[:200]}")
        return j
    raise RuntimeError(f"FrameFetch did not accept the key (HTTP {last.status_code}). Check FRAMEFETCH_API_KEY.")


def ff_extract(url):
    j = ff_post("/v1/extract", {
        "url": url,
        "fields": ["metadata", "transcript", "frames", "text_overlay"],
        "frames": {"mode": "fps", "fps": envnum("FRAMES_PER_SECOND", 0.5), "width": 360, "max_frames": 8},
    })
    if isinstance(j, dict) and "metadata" not in j and isinstance(j.get("data"), dict):
        j = j["data"]
    return j


def get_transcript(r):
    t = r.get("transcript")
    if isinstance(t, dict):
        return t.get("text") or " ".join(s.get("text", "") for s in t.get("segments", []) if isinstance(s, dict))
    return t if isinstance(t, str) else ""


def get_overlay_text(r):
    o = r.get("textOverlay") or r.get("text_overlay") or []
    seen, out = set(), []
    for item in o if isinstance(o, list) else []:
        txt = item.get("text") if isinstance(item, dict) else str(item)
        if isinstance(item, dict) and isinstance(item.get("lines"), list):
            txt = " ".join(str(x.get("text", x)) if isinstance(x, dict) else str(x) for x in item["lines"])
        txt = (txt or "").strip()
        if txt and txt.lower() not in seen:
            seen.add(txt.lower())
            out.append(txt)
    return out[:30]


def sniff_mime(b):
    if b.startswith(b"\x89PNG"):
        return "image/png"
    if b[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


def frame_to_bytes(f):
    """Frames may arrive as links, base64, or small objects. Handle all."""
    s = f
    if isinstance(f, dict):
        s = first(f, ["base64", "data", "b64", "image", "url", "signedUrl", "src"])
    if not isinstance(s, str) or not s:
        return None
    try:
        if s.startswith("http"):
            return requests.get(s, timeout=30).content
        if s.startswith("data:"):
            s = s.split(",", 1)[1]
        return base64.b64decode(s)
    except Exception:
        return None


def pick_frames(frames, k):
    """Keep the first 3 (the hook) and spread the rest evenly."""
    if not isinstance(frames, list) or not frames:
        return []
    if len(frames) <= k:
        return frames
    head = frames[:3]
    rest = frames[3:]
    need = k - len(head)
    idx = sorted({round(i * (len(rest) - 1) / max(1, need - 1)) for i in range(need)})
    return head + [rest[i] for i in idx]


# -------------------------------------------------------------------- Gemini
def gemini(parts, system=None, json_mode=False, temperature=0.3):
    key = env("GEMINI_API_KEY", required=True)
    primary = env("GEMINI_MODEL", "gemini-3.5-flash")
    # Backup models to auto-failover if Google has high demand on the primary
    models_to_try = [primary]
    for backup in ["gemini-3.6-flash", "gemini-3-flash-preview", "gemini-3.5-flash-lite"]:
        if backup not in models_to_try:
            models_to_try.append(backup)

    body = {"contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature}}
    if json_mode:
        body["generationConfig"]["responseMimeType"] = "application/json"
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    last_error = None
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        r = request_with_retry("POST", url, json=body, retry_busy=False,
                               headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        try:
            j = r.json()
        except ValueError:
            last_error = f"HTTP {r.status_code}"
            continue

        if r.status_code in (429, 503):
            last_error = f"Gemini {model} error {r.status_code}: {dig(j, 'error.message', 'busy')}"
            print(f"  ...{model} busy (503). Switching model in 2s...")
            time.sleep(2)
            continue

        if r.status_code >= 400:
            raise RuntimeError(f"Gemini error {r.status_code}: {dig(j, 'error.message', 'unknown')}")

        cands = j.get("candidates") or []
        if not cands:
            return ""
        return "".join(p.get("text", "") for p in dig(cands[0], "content.parts", []) or [])

    raise RuntimeError(f"Gemini service unavailable across all models. Last status: {last_error}")


CHECKLIST = """You are studying a short Instagram video/reel about: {topic}.
You get its caption, transcript, text shown on screen, and screenshots in time order
(the first screenshots are the opening seconds).
Return ONLY a JSON object with these keys:
hook_type: one of stat, question, myth_bust, story, shock, tip, news, mistake, transformation, other
hook_text: the first thing said or shown, max 20 words
presenter: one of creator, expert, owner, voiceover, text_only, none, unknown
format: one of talking_head, voiceover_on_footage, text_on_screen, tutorial, demonstration, before_after, carousel_style, other
topic: short string (2-4 words) describing the specific angle or subtopic covered in this video
emotion: one of curiosity, fear, hope, humor, nostalgia, surprise, trust, other
length_bucket: one of under_15s, 15_30s, 30_60s, over_60s, unknown
has_text_overlay: true or false
health_claims: list of short strings, the main claims, tips, or facts stated (empty list if none)
claims_cite_source: true if a source, study, or credential is named, otherwise false
claim_risk: one of low, medium, high (high = sounds unproven, dangerous, or misleading)
cta: one of follow, save, comment, share, link, none
why_it_works: one plain sentence
Use only what you can see or read. If unsure, use "unknown" or "other"."""


def analyze_reel(row, transcript, overlay, frames):
    topic = row["topic"] if ("topic" in row.keys() and row["topic"]) else "video content"
    checklist = CHECKLIST.format(topic=topic)
    text = (
        f"{checklist}\n\nCAPTION:\n{(row['caption'] or '')[:800]}\n\n"
        f"TRANSCRIPT:\n{(transcript or '(none)')[:3500]}\n\n"
        f"TEXT SHOWN ON SCREEN:\n{'; '.join(overlay) if overlay else '(none)'}"
    )
    parts = [{"text": text}]
    for f in frames:
        b = frame_to_bytes(f)
        if b:
            parts.append({"inline_data": {"mime_type": sniff_mime(b),
                                          "data": base64.b64encode(b).decode()}})
    try:
        out = parse_json_loose(gemini(parts, json_mode=True, temperature=0.1))
        if isinstance(out, dict):
            return out
    except RuntimeError:
        pass

    # Free-tier fallback: analyze using transcript, caption, and OCR text without image overhead
    print("  ...image processing busy, falling back to transcript & OCR analysis...")
    out = parse_json_loose(gemini([{"text": text}], json_mode=True, temperature=0.1))
    return out if isinstance(out, dict) else None


def length_bucket_from_seconds(sec):
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return None
    return "under_15s" if sec < 15 else "15_30s" if sec < 30 else "30_60s" if sec < 60 else "over_60s"


def process_reel(con, row):
    url = row["url"]
    print(f"  reading {url}")
    try:
        r = ff_extract(url)
        transcript = get_transcript(r)
        overlay = get_overlay_text(r)
        frames = r.get("frames") or []
        meta = r.get("metadata") or {}
        picked = pick_frames(frames, int(envnum("MAX_FRAMES_TO_AI", 8)))
        if not picked and row["thumb"]:
            picked = [row["thumb"]]  # fall back to the cover image
        duration = row["duration"] or first(meta, ["durationSec", "duration"])
        analysis = analyze_reel(row, transcript, overlay, picked)
        if not analysis:
            raise RuntimeError("AI did not return a readable checklist")
        lb = length_bucket_from_seconds(duration)
        if lb:
            analysis["length_bucket"] = lb
        views, likes, comments = row["views"] or 0, row["likes"] or 0, row["comments"] or 0
        score = (likes + comments) / views if views >= 100 else ((likes + comments) if (likes or comments) else None)
        con.execute(
            """UPDATE reels SET transcript=?, overlay=?, frame_count=?, analysis=?, score=?,
               duration=?, status='done', error=NULL WHERE url=?""",
            (transcript, json.dumps(overlay), len(frames), json.dumps(analysis), score, duration, url),
        )
    except Exception as e:  # keep going if one reel fails
        msg = str(e)[:300]
        print(f"    skipped: {msg}")
        con.execute("UPDATE reels SET status='failed', error=? WHERE url=?", (msg, url))
    con.commit()


def choose_reels(con, limit, topic=None):
    """Pick reels spread from most engaged to least engaged, prioritizing the given topic."""
    rows = []
    if topic:
        rows = con.execute(
            "SELECT * FROM reels WHERE status IN ('found') AND topic=? ORDER BY views DESC, likes DESC, comments DESC",
            (topic,),
        ).fetchall()
    if not rows:
        rows = con.execute(
            "SELECT * FROM reels WHERE status IN ('found') ORDER BY views DESC, likes DESC, comments DESC"
        ).fetchall()
    if len(rows) <= limit:
        return rows
    step = (len(rows) - 1) / max(1, limit - 1)
    return [rows[round(i * step)] for i in range(limit)]


def process_single_reel_isolated(row):
    """Processes a single reel in its own thread with a dedicated SQLite connection."""
    con = sqlite3.connect(DB_PATH, timeout=30.0)
    con.row_factory = sqlite3.Row
    try:
        process_reel(con, row)
    finally:
        con.close()


def process_reels_parallel(rows, max_workers=4, on_progress=None):
    """Processes multiple reels concurrently using a thread pool."""
    if not rows:
        return
    completed = 0
    total = len(rows)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_row = {executor.submit(process_single_reel_isolated, row): row for row in rows}
        for future in as_completed(future_to_row):
            row = future_to_row[future]
            try:
                future.result()
            except Exception as e:
                print(f"  error processing {row['url']}: {e}")
            completed += 1
            if on_progress:
                on_progress(completed, total, row["url"])


# -------------------------------------------------------------------- report
TAGS = ["hook_type", "presenter", "format", "topic", "emotion", "length_bucket", "cta"]


def tag_share(analyses, key):
    c = Counter(str(a.get(key, "unknown")) for a in analyses)
    n = max(1, len(analyses))
    return {k: round(100 * v / n) for k, v in c.most_common(4)}


def build_report(con, topic):
    # Prioritize reels specifically matching the given topic
    rows = []
    if topic:
        rows = con.execute(
            "SELECT * FROM reels WHERE status='done' AND topic=? ORDER BY score DESC",
            (topic,),
        ).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT * FROM reels WHERE status='done' AND topic LIKE ? ORDER BY score DESC",
                (f"%{topic}%",),
            ).fetchall()
    if not rows:
        rows = con.execute("SELECT * FROM reels WHERE status='done' ORDER BY score DESC").fetchall()

    if not rows:
        print(f"No analyzed reels yet for '{topic}'. Run: python dogtrend.py run \"{topic}\"")
        return None
    scored = [r for r in rows if r["score"] is not None]
    k = max(2, math.ceil(len(scored) * 0.2)) if len(scored) >= 6 else max(1, len(scored) // 3)
    top, bottom = scored[:k], scored[-k:] if len(scored) > 2 * k - 1 else []
    parse = lambda rs: [json.loads(r["analysis"]) for r in rs]

    lines = [f"Reels analyzed: {len(rows)} (with usable numbers: {len(scored)})",
             f"Top group size: {len(top)}, bottom group size: {len(bottom)}"]
    for key in TAGS:
        lines.append(f"{key}: top={tag_share(parse(top), key)} | bottom={tag_share(parse(bottom), key)}"
                     if bottom else f"{key}: all={tag_share(parse(rows), key)}")
    risky = [a for a in parse(rows) if a.get("claim_risk") in ("medium", "high")]
    lines.append(f"Reels with medium/high risk health claims: {len(risky)}")

    show = env("SHOW_HANDLES", "false").lower() == "true"
    examples = []
    for i, r in enumerate(top[:5], 1):
        a = json.loads(r["analysis"])
        who = f" (@{r['handle']})" if show and r["handle"] else ""
        stats = f"likes={r['likes']}, comments={r['comments']}" + (f", views={r['views']}" if r["views"] else "")
        examples.append(
            f"Winner {i}{who}: {stats}, score={r['score']:.1f}, "
            f"hook=\"{a.get('hook_text')}\", type={a.get('hook_type')}, topic={a.get('topic')}, "
            f"why={a.get('why_it_works')}, transcript_start=\"{(r['transcript'] or '')[:250]}\"")
    flops = []
    for i, r in enumerate(bottom[:3], 1):
        a = json.loads(r["analysis"])
        stats = f"likes={r['likes']}, comments={r['comments']}" + (f", views={r['views']}" if r["views"] else "")
        flops.append(f"Weak {i}: {stats}, score={r['score']:.1f}, "
                     f"hook=\"{a.get('hook_text')}\", type={a.get('hook_type')}, topic={a.get('topic')}")
    claims = Counter(c for a in parse(rows) for c in (a.get("health_claims") or []))

    prompt = f"""Write a weekly content report for a creator.
Topic: {topic}
Creator goal: {env('GOAL')}
On camera: {env('ON_CAMERA')}
Audience: {env('AUDIENCE')}

DATA FROM THE REELS WE READ
{chr(10).join(lines)}

BEST REELS
{chr(10).join(examples) or 'none'}

WEAKEST REELS
{chr(10).join(flops) or 'none'}

COMMON HEALTH CLAIMS SEEN: {dict(claims.most_common(8))}

Write the report in markdown with these sections:
1. Make these 3 videos this week (Actionable priority: for each video, provide:
   - **Hook (0–2s)**: The exact opening words and on-screen hook text to stop the scroll.
   - **The Script**: A complete, natural 30–40 second word-for-word spoken script.
   - **Video Direction (Type of video to shoot)**: Visual format (e.g. POV bowl prep, talking head, B-roll montage), camera angle/setup, scene-by-scene visual actions, and on-screen text overlays.
   - **Audio Direction**: Voice delivery tone (e.g. warm, urgent, playful), sound effects/ASMR (e.g. bowl clinking, paw sounds), and background music style (e.g. trending lo-fi, warm acoustic, emotional piano).
   - **The Data**: Specific winning reels, likes, comments, and views from the data proving why this works)
2. What is working (3 to 5 plain points, citing the likes, comments, and views from the data)
3. What is not working (flops and habits to avoid)
4. Two more video ideas (bonus concepts for later, each with quick Hook, Script, Video direction, and Audio direction)
5. Health claims to check before posting (list the claims seen in the data and say what needs a veterinary source)
6. Honest limits (sample size, what the data cannot prove)
7. What could work next (experiments and angles to test next)

Rules: use simple everyday words, write scripts the way a real person talks, no buzzwords, no hype.
Cite the real numbers (likes and comments, and views if present) from the data above.
If the sample is small (under 20 reels), say so clearly and call the findings early signals, not proof."""
    text = gemini([{"text": prompt}], temperature=0.5)
    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / f"report_{datetime.now():%Y-%m-%d_%H%M}.md"
    out.write_text(f"# Weekly report: {topic}\n\n_Made on {datetime.now():%d %b %Y}. "
                   f"A person must check every health claim before posting._\n\n{text}\n",
                   encoding="utf-8")
    print(f"\nReport saved: {out}")
    return out


# ------------------------------------------------------------------ commands
def make_phrases(topic):
    n = int(envnum("SEARCH_PHRASES", 6))
    prompt = (f"Give {n} short, highly searched Instagram search phrases (2 to 4 words each) that creators and viewers use "
              f"on Instagram for this specific topic: '{topic}'. Cover different popular subtopics, common problems, queries, and variations. "
              f"Return only a JSON list of strings.")
    try:
        out = parse_json_loose(gemini([{"text": prompt}], json_mode=True, temperature=0.4))
        if isinstance(out, list) and out:
            return [str(x) for x in out][:n]
    except RuntimeError as e:
        print(f"  (AI search phrases failed: {e}; using simple ones)")
    return [topic, f"{topic} tips", f"best {topic}", f"{topic} hacks", f"how to {topic}"][:n]


def cmd_check(_):
    print("Checking your three keys (this costs almost nothing)...")
    try:
        sc_get("/instagram/search/reels", {"query": "dog", "dry_run": 1})
        print("  SocialCrawl: OK")
    except Exception as e:
        print(f"  SocialCrawl: PROBLEM - {e}")
    try:
        ff_post("/v1/metadata", {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"})
        print("  FrameFetch:  OK")
    except Exception as e:
        print(f"  FrameFetch:  PROBLEM - {e}")
    try:
        gemini([{"text": "Reply with the single word OK."}])
        print("  Gemini:      OK")
    except Exception as e:
        print(f"  Gemini:      PROBLEM - {e}")


def cmd_run(args):
    con = db()
    topic = args.topic
    print(f"Topic: {topic}\n1) Making search phrases")
    phrases = make_phrases(topic)
    print("   ", phrases)
    print("2) Finding reels")
    discover(con, topic, phrases, limit=limit)
    todo = choose_reels(con, limit, topic=topic)
    print(f"3) Reading {len(todo)} reels in parallel (screenshots, transcript, on-screen text, AI notes)")
    process_reels_parallel(todo, max_workers=4)
    print("4) Writing report")
    build_report(con, topic)


def cmd_report(args):
    build_report(db(), args.topic or "dog longevity")


def main():
    global DEBUG
    ap = argparse.ArgumentParser(description="Find, read and learn from Instagram reels.")
    ap.add_argument("--debug", action="store_true", help="save raw service replies to data/debug/")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    p = sub.add_parser("run")
    p.add_argument("topic")
    p.add_argument("--limit", type=int)
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("topic", nargs="?")
    p.set_defaults(fn=cmd_report)
    args = ap.parse_args()
    DEBUG = args.debug
    args.fn(args)


if __name__ == "__main__":
    main()
