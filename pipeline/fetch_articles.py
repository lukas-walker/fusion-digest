#!/usr/bin/env python3
"""Deep-dive fetch for the ≤5 selected groups — the only pages the model reads.

Reads pipeline/out/selected.json (written by the triage step), fetches each URL,
extracts clean main-text (trafilatura for HTML, markitdown/pdfminer for PDFs),
truncates, and writes one compact markdown bundle per group into
pipeline/out/bundles/. The writing step then reads those bundles instead of ever
fetching a page itself.

selected.json shape:
    {"groups": [
        {"slug": "east-1066s", "headline": "...", "urls": ["https://...", "..."]},
        ...
    ]}

Usage:
    python pipeline/fetch_articles.py [pipeline/out/selected.json] [--max-chars 8000]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import urllib.request

OUT = Path(__file__).resolve().parent / "out"
BUNDLES = OUT / "bundles"
UA = "Mozilla/5.0 (FusionDigest research bot; +https://github.com/lukas-walker/fusion-digest)"


def fetch(url: str, timeout: int = 30) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
        return r.read(), r.headers.get("Content-Type", "")


def html_to_text(raw: bytes, url: str) -> str:
    try:
        import trafilatura  # type: ignore
        txt = trafilatura.extract(
            raw.decode("utf-8", "replace"), url=url,
            include_comments=False, include_tables=True, favor_precision=True,
        )
        if txt:
            return txt
    except Exception:  # noqa: BLE001
        pass
    # crude fallback: strip tags
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw.decode("utf-8", "replace"))
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def pdf_to_text(raw: bytes) -> str:
    tmp = OUT / "_tmp.pdf"
    tmp.write_bytes(raw)
    try:
        from markitdown import MarkItDown  # type: ignore
        return MarkItDown().convert(str(tmp)).text_content
    except Exception:  # noqa: BLE001
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            return extract_text(str(tmp)) or ""
        except Exception:  # noqa: BLE001
            return "[PDF text extraction unavailable — install markitdown or pdfminer.six]"
    finally:
        tmp.unlink(missing_ok=True)


def extract(url: str, max_chars: int) -> str:
    try:
        raw, ctype = fetch(url)
    except Exception as e:  # noqa: BLE001
        return f"[could not fetch: {e}]"
    is_pdf = "pdf" in ctype.lower() or url.lower().endswith(".pdf")
    text = pdf_to_text(raw) if is_pdf else html_to_text(raw, url)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + " …[truncated]"
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("selected", nargs="?", default=str(OUT / "selected.json"))
    ap.add_argument("--max-chars", type=int, default=8000, help="per-article cap")
    args = ap.parse_args()

    data = json.loads(Path(args.selected).read_text())
    groups = data.get("groups", [])
    BUNDLES.mkdir(parents=True, exist_ok=True)

    for i, g in enumerate(groups, 1):
        slug = g.get("slug") or f"group-{i}"
        parts = [f"# {g.get('headline', slug)}\n"]
        for url in g.get("urls", []):
            print(f"[{i}] {slug}: fetching {url}", file=sys.stderr)
            parts.append(f"\n## Source: {url}\n\n{extract(url, args.max_chars)}\n")
        (BUNDLES / f"{i:02d}-{slug}.md").write_text("\n".join(parts))
    print(f"\n{len(groups)} bundles -> pipeline/out/bundles/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
