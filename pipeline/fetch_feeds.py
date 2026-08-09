#!/usr/bin/env python3
"""Discovery step for Fusion Digest — the cheap, LLM-free pass.

Collects candidate articles from the source RSS/Atom feeds and the arXiv API,
windowed to everything published since the last successful run (minus a safety
overlap so late/back-dated items are never dropped), and deduplicated against
state/seen.json. Writes a compact pipeline/out/candidates.json for the triage
step. The model only ever sees this small JSON — never raw HTML.

Network egress to the source hosts is required. Individual source failures are
logged to stderr and skipped, so partial coverage still produces output.

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
from pathlib import Path

try:
    import feedparser  # type: ignore
except ImportError:
    sys.exit("fetch_feeds.py needs feedparser — run: pip install -r pipeline/requirements.txt")

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state"
OUT = Path(__file__).resolve().parent / "out"
SEEN_PATH = STATE / "seen.json"
LAST_RUN_PATH = STATE / "last_run.txt"

# Best-known feed URLs. Unreachable or empty ones are skipped; adjust after the
# first live run if a source has moved its feed.
FEEDS = {
    "Physics World": "https://physicsworld.com/c/particle-nuclear/nuclear-fusion/feed/",
    "Phys.org (plasma)": "https://phys.org/rss-feed/physics-news/plasma-physics/",
    "World Nuclear News": "https://world-nuclear-news.org/rss",
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


def entry_date(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    return None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def prev_month_start(today: dt.date) -> dt.date:
    first = today.replace(day=1)
    last_prev = first - dt.timedelta(days=1)
    return last_prev.replace(day=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", help="target month YYYY-MM (default: the month that just ended)")
    ap.add_argument("--overlap-days", type=int, default=7)
    ap.add_argument("--max", type=int, default=300, help="cap on candidates emitted")
    args = ap.parse_args()

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
            feed = feedparser.parse(url)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {source}: {e}", file=sys.stderr)
            continue
        if getattr(feed, "bozo", 0) and not feed.entries:
            print(f"  ! {source}: no entries ({url})", file=sys.stderr)
            continue
        kept = 0
        for e in feed.entries:
            link = e.get("link", "")
            if not link:
                continue
            when = entry_date(e)
            if when and when < window_start:
                continue  # too old for this window
            title = clean_text(e.get("title", ""), 300)
            summary = clean_text(e.get("summary", e.get("description", "")))
            is_arxiv = source.startswith("arXiv")
            if is_arxiv and not KEYWORDS.search(f"{title} {summary}"):
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
