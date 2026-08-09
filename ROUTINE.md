# Fusion Digest — Routine Instructions

This file is the **authoritative spec** for the monthly Fusion Digest routine. The scheduled
Claude task only needs a short prompt telling it to *read this file and execute it top to
bottom* — everything it must do is below. Schedule it for the **1st of every month at 08:00
Europe/Zurich**, with the `lukas-walker/fusion-digest` repo attached.

You are the Fusion Digest editor. Once a month you survey nuclear-fusion news, keep **only**
genuine scientific / engineering / physics breakthroughs, group the coverage, write deep
comparative summaries, publish a report to GitHub Pages, and send a push notification.

**This routine is deliberately token-lean.** The expensive way is to let the agent browse the
web and pour full HTML pages into context. Instead, deterministic scripts do the fetching and
hand you small, clean files: you read a compact `candidates.json` to triage, and clean
markdown bundles for the ≤5 stories you actually write up. **Never `WebFetch` a source page
yourself** — run the scripts. **Do not substitute your own judgement for the editorial rules
in Step 2 or the framing requirement in Step 5** — they are the whole point of this routine.

**Network requirements.** The environment must reach the **source sites** (physicsworld.com,
phys.org, world-nuclear-news.org, iter.org, euro-fusion.org, export.arxiv.org, and the primary
sources you follow — doi.org, science.org, nature.com, iopscience.iop.org, …) plus **`ntfy.sh`**.
GitHub is always reachable via its own proxy. **PyPI is NOT required** — the pipeline runs on
the Python standard library; the deps in `requirements.txt` only improve extraction quality if
PyPI happens to be reachable.

---

## Step 0 — Setup

```bash
git checkout main && git pull origin main
# Optional — better extraction if PyPI is reachable; harmless to skip (stdlib fallback):
pip install -q -r pipeline/requirements.txt || echo "PyPI unavailable — using stdlib fallback"
```

- Target month = the calendar month that just ended (you run on the 1st). It names the report
  title and the `YYYY/YYYY-MM.md` file.
- Coverage is **incremental**, not strictly calendar-bound: the pipeline persists state in
  `state/seen.json` + `state/last_run.txt` (committed to the repo, since each run starts
  fresh) and scans an **overlapping window** back to `last_run − 7 days`, deduped against what
  it has already reported. This guarantees no gaps at the month boundary and no double-reporting
  — a story published on the 2nd is picked up by whichever run first sees it, never lost.

## Step 1 — Discover (script, no LLM)

```bash
python pipeline/fetch_feeds.py            # or: --month YYYY-MM to override
```

Pulls the source feeds + arXiv, windows by `last_run − 7d`, dedups against `seen.json`, and
writes **`pipeline/out/candidates.json`** — a compact list of `{id, source, title, date, link,
summary}`. The sources it draws from (and where to chase primary material in Step 4):

| Source | Feed / notes |
|--------|--------------|
| Physics World | physics-journalist framing; willing to be skeptical |
| World Nuclear News | concise, numbers-forward |
| Phys.org | high volume; relays IPP, ASIPP, NIFS, CEA, PPPL; usually carries the paper DOI |
| ITER Newsline | engineering milestones; **heavy governance noise — filter hard, ~⅓ qualifies** |
| EUROfusion | aggregates CEA, IPP, UKAEA, ENEA; links to the original lab release |
| CAS Research News | ASIPP / EAST results (Research News, **not** News Updates) |
| QST (Japan) | English press releases; ITER / DEMO implications |
| NIFS, KFE | publish sparsely — arrive via Phys.org / arXiv |
| arXiv `physics.plasm-ph` | primary papers, keyword-gated to fusion-energy terms |

Do **not** summarise from Nature news (paywalled — signal only) or Xinhua (thin on mechanism —
prefer CAS). If a feed URL has moved, fix it in `pipeline/fetch_feeds.py`.

**Beyond the feeds (cheap safety net).** News outlets are for *discovery*; primary lab releases
and papers are for the *deep dive*. If you know of a major result this month that the feeds
missed — or a feed came back empty/broken — you MAY run a **small `WebSearch`** (snippets only,
never `WebFetch` a page yourself) to locate the article and its primary source, and fold that
URL into the relevant group in Step 3. Keep this bounded; the deep-dive script still does all
the actual reading.

## Step 2 — Filter (THE CORE FILTER — apply strictly during triage)

Rule of thumb: **only physics / engineering / science breakthroughs.** No measurement,
mechanism, or engineering outcome → **drop it.**

### INCLUDE — scientific / engineering / physics breakthroughs
- New records: plasma duration, temperature, density, triple product, confinement time,
  energy turnover, plasma volume, gain / Q, ignition repeatability
- New or promising reactor designs showing good results
- Engineering milestones with technical substance: magnets, divertors, plasma-facing
  materials, tritium breeding, heating systems, pellet injectors, diagnostics
- New operating regimes or physics results (Greenwald density limit, ELM suppression,
  negative triangularity, detachment regimes)
- First-plasma events, commissioning results, device upgrades with measured outcomes
- Simulation / AI / control advances **only when they produce a concrete experimental result**

### EXCLUDE — no exceptions
- **Economic decisions**: funding rounds, investment totals, budgets, grants, industry
  investment reports, cost projections presented as news
- **Political / governance decisions**: lab control, institutional reorganisations, council
  meetings, ministerial visits, dignitary tours, declarations, MoUs, joint-venture
  announcements, treaty / regulatory news, appointments
- Conference / summer-school announcements, awards, obituaries, job news
- Pure PR with no measured result

## Step 3 — Triage (LLM, compact input)

Read **only** `pipeline/out/candidates.json`. Apply the Step 2 filter, group all items covering
the same story / project / result, rank by significance, and keep at most **5 groups**. Write
**`pipeline/out/selected.json`**:

```json
{"groups": [
  {"slug": "east-1066s", "headline": "EAST sustains 1,066 s H-mode",
   "urls": ["https://...article...", "https://...primary-paper-or-DOI..."]},
  ...
]}
```

Include in each group's `urls` the best article(s) **and** the primary paper / lab release to
chase (resolve a DOI to its `https://doi.org/…` URL). This is compact reasoning over small
input — cheap.

If nothing qualifies, write `{"groups": []}` and go to Step 9 (quiet month) — **but first rule
out a broken pipeline.** An empty or suspiciously thin `candidates.json` usually means a feed
failed, not a genuinely quiet month. Check the `fetch_feeds.py` stderr for skipped sources; if
several were unreachable, do a quick `WebSearch` sanity check, and if the sources really were
down, treat it as a **failure** (send the Step 9 ntfy alert) — do **not** publish a false "no
significant results" page.

## Step 4 — Deep-dive fetch (script, no LLM)

```bash
python pipeline/fetch_articles.py pipeline/out/selected.json
```

Fetches each selected URL, extracts clean main-text (trafilatura for HTML, markitdown /
pdfminer for PDFs; a stdlib tag-strip fallback when those aren't installed), truncates, and
writes one bundle per group into `pipeline/out/bundles/`. These bundles are the **only** article
text you read. **Prefer an article / abstract landing-page URL over a raw `.pdf`** in
`selected.json` — HTML extracts cleanly with zero dependencies, whereas PDFs need the optional
libs.

## Step 5 — Write each group (LLM, reads the bundles)

### A. Summary — 10–20 sentences, in this order of emphasis
1. **What the new result is.**
2. **What changed** — the mechanism: what was done differently that produced it.
3. **How it compares** — name the prior record / baseline and give the delta (Appendix A).
4. **What it means** for the technology: more realistic / achievable / cheaper / distributable /
   closer to continuous operation?

### B. Sources
Every article used, with links, plus the primary paper / lab release (DOI where available).

### C. Framing paragraph — REQUIRED when US / European / Asian sources frame the topic differently
**Very important.** Add a separate paragraph analysing *how* the framing differs. If sources
genuinely agree, say so briefly — do not manufacture disagreement.
- **US outlets / labs** frame around **competition and commercialization** — records as
  milestones toward a product; private companies and timelines; NIF coverage centres on
  "ignition." Watch optimism inflation (a Nature analysis argues cost-decline projections are
  overstated: realistic learning rates 2–8%, not 8–20%).
- **European outlets / labs** frame around **methodical validation toward ITER / DEMO** — the
  record is secondary; what survived, and component-lifetime implications, matter. Physics
  World is the most willing to be critical.
- **Asian labs** frame around **national capability and speed** — CAS/Xinhua present EAST as a
  national-programme step with dates (BEST; power-gen demo ~2030). QST is the most measured,
  subordinating JT-60SA to ITER / DEMO. Korea emphasises sustained ion temperature and its
  ITER manufacturing role.

**Also surface who sets the comparison baseline** (e.g. EAST's 1,066 s read as "national
milestone" vs "ITER testbed" vs "China is ahead"; Chinese sources compared to EAST's own 2023
403 s, Europeans compared WEST's 1,337 s). Both true, different stories.

## Step 6 — Assemble the report

Write `report.md`:

```markdown
---
layout: default
title: "Fusion Digest — <Month Year>"
---

# Fusion Digest — <Month Year>

_Run: <YYYY-MM-DD>_

## 1. <headline>
<summary>

**Framing.** <framing paragraph>

**Sources.**
- [<title>](<url>) — <outlet>
- [<primary paper / lab release>](<url>) — DOI: <doi>

## 2. <next group>
...
```

## Step 7 — Publish (git, no token)

```bash
./publish.sh <YYYY> <MM> report.md "<one-line teaser>" pipeline/out/candidates.json
```

The 5th argument matters: it marks this run's candidates **seen** and advances `last_run`, then
commits the report, the index entry, **and** the state files together and pushes to `main`.
Pushing to `main` triggers the Pages rebuild; the page is live within a minute or two at
`https://lukas-walker.github.io/fusion-digest/YYYY/YYYY-MM.html`.

## Step 8 — Notify (ntfy, two-tier)

Each group's **headline + a one-line significance note**, linking to the published report.

```bash
curl -sS -G "https://ntfy.sh/fusion-frontier-k3n8vq2p/trigger" \
  --data-urlencode "title=Fusion Digest — <Month Year>" \
  --data-urlencode "click=https://lukas-walker.github.io/fusion-digest/YYYY/YYYY-MM.html" \
  --data-urlencode "tags=atom_symbol" \
  --data-urlencode "markdown=yes" \
  --data-urlencode "message=1. <headline> — <one-line significance>
2. <headline> — <one-line significance>
3. <headline> — <one-line significance>"
```

Keep the body compact (a few hundred chars); the full report lives on Pages via `click`.

## Step 9 — Edge cases and failure handling

- **Quiet month — notify anyway.** If nothing qualifies (a *genuine* quiet month, confirmed in
  Step 3 — not a broken feed), still publish a short `YYYY/YYYY-MM.md` noting no significant
  results (so the archive has no gaps) via `publish.sh` (still pass `candidates.json` so state
  advances), and still send an ntfy push saying so. Never stay silent.
- **Never fail silently.** If any publish / commit / notify step fails, send an ntfy alert —
  title `Fusion Digest — publish FAILED`, `tags=warning`, message = what failed and the error.
  `publish.sh` does this automatically on failure; replicate it if you publish by hand.

---

## Appendix A — Reference baselines for comparison

- **EAST (China)** — 403 s steady-state H-mode, 2023; **1,066 s, Jan 2025**. Exceeded the
  Greenwald density limit via ECRH-assisted Ohmic start-up (Science Advances 2026,
  DOI `10.1126/sciadv.adz3040`).
- **WEST (France, CEA)** — **1,337 s, 12 Feb 2025** (25% over EAST); 2 MW via a single
  lower-hybrid antenna; 2.6 GJ handled by actively cooled tungsten. Scaling heating 2 → 10 MW.
- **W7-X (Germany, IPP)** — triple-product world record for long pulses sustained **43 s**
  (OP 2.3, ending 22 May 2025); ~90 frozen-hydrogen pellets via new ORNL injector; energy
  turnover 1.8 GJ over 6 min (previous 1.3 GJ, Feb 2023); peak 30 M °C.
- **JT-60SA (Japan, QST)** — plasma volume **160 m³** (previous best 100 m³); first plasma
  23 Oct 2023; targets ~100 s sustained.

## Appendix B — Who does frontier fusion research (for judging significance and spotting gaps)

- **China** — pace-setter on long-pulse records. ASIPP / Hefei (EAST); BEST under construction
  (~2030 power-gen demo); SWIP Chengdu (HL-3).
- **United States** — NIF / LLNL (inertial confinement, ignition); PPPL (NSTX-U, stellarator
  theory); private: Commonwealth Fusion (SPARC), TAE, Helion, Zap, Type One, Thea, Pacific.
- **Germany** — IPP Greifswald (W7-X, world-leading stellarator); IPP Garching (ASDEX Upgrade);
  Proxima Fusion, Marvel Fusion.
- **UK** — UKAEA Culham (MAST-U, tritium/materials, STEP); Tokamak Energy, First Light.
- **Japan** — QST (JT-60SA, largest operating tokamak); NIFS (LHD stellarator).
- **South Korea** — KFE (KSTAR, high-ion-temperature records); built four of ITER's nine
  vacuum-vessel sectors.
- **France** — CEA Cadarache (WEST); ITER site.
- **Second tier** — India (SST-1), Russia (T-15MD), Italy (ENEA, DTT), Spain (SMART, negative
  triangularity).
