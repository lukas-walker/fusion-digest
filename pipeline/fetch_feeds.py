#!/usr/bin/env python3
"""Discovery step for Fusion Digest — the cheap, LLM-free pass.

Collects candidate articles from the source RSS/Atom feeds and the arXiv API,
windowed to everything published since the last successful run (minus a safety
overlap so late/back-dated items are never dropped), and deduplicated against
state/seen.json. Writes a compact pipeline/out/candidates.json for the triage
step. The model only ever sees this small JSON — never raw HTML.

Runs on the Python standard library alone (urllib + ElementTree); if feedparser
is installed it is used for slightly more robust parsing, but it is optional —
so this works even when the environment's egress blocks PyPI. Only the source
hosts need to be reachable. Individual source failures are logged to stderr and
skipped, so partial coverage still produces output.

Usage:
    python pipeline/fetch_feeds.py [--month YYYY-MM] [--overlap-days 7] [--max 300]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import feedparser  # optional; stdlib fallback below when absent
    HAVE_FEEDPARSER = True
except ImportError:
    HAVE_FEEDPARSER = False

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = Path(__file__).resolve().parent / "out"
SEEN_PATH = STATE / "seen.json"
LAST_RUN_PATH = STATE / "last_run.txt"
UA = "Mozilla/5.0 (FusionDigest research bot; +https://github.com/lukas-walker/fusion-digest)"

# Best-known feed URLs. Unreachable or empty ones are skipped; adjust after the
# first live run if a source has moved its feed.
FEEDS = {
    "Physics World": "https://physicsworld.com/c/particle-nuclear/nuclear-fusion/feed/",
    "Phys.org (plasma)": "https://phys.org/rss-feed/physics-news/plasma-physics/",
    "World Nuclear News": "https://world-nuclear-news.org/rss/news",
    "ITER Newsline": "https://www.iter.org/whatsnew/rss",
    "EUROfusion": "https://euro-fusion.org/feed/",
}
ARXIV_FEED = (
    "http://export.arxiv.org/api/query?search_query=cat:physics.plasm-ph"
    "&sortBy=submittedDate&sortOrder=descending&max_results=150"
)

# Keep the fusion-energy signal; drop unrelated plasma physics (astro, etc.).
KEYWORDS = re.compile(
    r"\b(fusion|tokamak|stellarator|confinement|triple[\s-]?product|ignition|"
    r"divertor|iter|east|west|w7-?x|kstar|jt-?60|pellet|tritium|greenwald|"
    r"h-mode|q\s*=|gain|plasma-?facing|breeding)\b",
    re.IGNORECASE,
)


def norm_url(url: str) -> str:
    u = (url or "").strip().split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u).lower()


def art_id(url: str) -> str:
    return hashlib.sha1(norm_url(url).encode()).hexdigest()[:16]


def clean_text(s: str, limit: int = 500) -> str:
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(raw: str) -> dt.datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:  # RFC 822, e.g. "Tue, 05 Aug 2026 12:00:00 GMT"
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:  # ISO 8601 / Atom, e.g. "2026-08-05T12:00:00Z"
            d = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d is not None and d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def entries_feedparser(url: str) -> list[dict]:
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", 0) and not feed.entries:
        raise RuntimeError(getattr(feed, "bozo_exception", "parse error"))
    out = []
    for e in feed.entries:
        when = None
        for key in ("published_parsed", "updated_parsed"):
            t = e.get(key)
            if t:
                when = dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
                break
        out.append({
            "link": e.get("link", ""),
            "title": e.get("title", ""),
            "summary": e.get("summary", e.get("description", "")),
            "when": when,
        })
    return out


def entries_stdlib(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        data = r.read()
    root = ET.fromstring(data)
    for el in root.iter():  # strip XML namespaces so tags are bare
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]

    def field(item, *names) -> str:
        for n in names:
            el = item.find(n)
            if el is not None and (el.text or el.get("href")):
                return (el.text or el.get("href") or "").strip()
        return ""

    items = root.findall(".//item") or root.findall(".//entry")  # RSS or Atom
    out = []
    for it in items:
        link = ""
        le = it.find("link")
        if le is not None:
            link = (le.text or le.get("href") or "").strip()
        if not link:
            link = field(it, "id", "guid")
        out.append({
            "link": link,
            "title": field(it, "title"),
            "summary": field(it, "summary", "description", "content"),
            "when": parse_date(field(it, "pubDate", "published", "updated", "date")),
        })
    return out


def fetch_entries(url: str) -> list[dict]:
    if HAVE_FEEDPARSER:
        return entries_feedparser(url)
    return entries_stdlib(url)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def prev_month_start(today: dt.date) -> dt.date:
    first = today.replace(day=1)
    return (first - dt.timedelta(days=1)).replace(day=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="target month YYYY-MM (default: the month that just ended)")
    ap.add_argument("--overlap-days", type=int, default=7)
    ap.add_argument("--max", type=int, default=300, help="cap on candidates emitted")
    args = ap.parse_args()

    print(f"feedparser: {'yes' if HAVE_FEEDPARSER else 'no (stdlib fallback)'}", file=sys.stderr)

    now = dt.datetime.now(dt.timezone.utc)
    if args.month:
        y, m = (int(x) for x in args.month.split("-"))
        month_start = dt.date(y, m, 1)
    else:
        month_start = prev_month_start(now.date())

    # Window start = earliest of (target month start, last successful run),
    # pulled back by the overlap. Guarantees no gap even if a run was late.
    last_run_raw = LAST_RUN_PATH.read_text().strip() if LAST_RUN_PATH.exists() else ""
    anchors = [dt.datetime.combine(month_start, dt.time(tzinfo=dt.timezone.utc))]
    if last_run_raw:
        try:
            anchors.append(dt.datetime.fromisoformat(last_run_raw))
        except ValueError:
            pass
    window_start = min(anchors) - dt.timedelta(days=args.overlap_days)

    seen = load_json(SEEN_PATH, {})
    print(f"window: {window_start.date()} .. {now.date()}  |  {len(seen)} already seen",
          file=sys.stderr)

    candidates: dict[str, dict] = {}
    for source, url in {**FEEDS, "arXiv (plasm-ph)": ARXIV_FEED}.items():
        try:
            entries = fetch_entries(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {source}: {e}", file=sys.stderr)
            continue
        if not entries:
            print(f"  ! {source}: no entries ({url})", file=sys.stderr)
            continue
        kept = 0
        for e in entries:
            link = e["link"]
            if not link:
                continue
            when = e["when"]
            if when and when < window_start:
                continue  # too old for this window
            title = clean_text(e["title"], 300)
            summary = clean_text(e["summary"])
            if source.startswith("arXiv") and not KEYWORDS.search(f"{title} {summary}"):
                continue  # unrelated plasma paper
            aid = art_id(link)
            if aid in seen or aid in candidates:
                continue
            candidates[aid] = {
                "id": aid,
                "source": source,
                "title": title,
                "date": when.date().isoformat() if when else "",
                "link": link,
                "summary": summary,
            }
            kept += 1
        print(f"  - {source}: {kept} new", file=sys.stderr)

    items = sorted(candidates.values(), key=lambda c: c["date"], reverse=True)[: args.max]
    OUT.mkdir(exist_ok=True)
    out = {
        "generated": now.isoformat(),
        "target_month": month_start.strftime("%Y-%m"),
        "window_start": window_start.isoformat(),
        "count": len(items),
        "candidates": items,
    }
    (OUT / "candidates.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\n{len(items)} candidates -> pipeline/out/candidates.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
