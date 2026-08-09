# Fusion Digest

A monthly, automated research digest of **scientific and engineering breakthroughs in
nuclear fusion for energy production**. Every month a Claude scheduled task surveys the
fusion-news landscape, filters it hard for genuine physics / engineering results, groups
related coverage, writes deep comparative summaries, publishes a report to GitHub Pages,
and sends a push notification.

**Live site:** <https://lukas-walker.github.io/fusion-digest/>

## What it covers

**In scope** — new records (plasma duration, temperature, density, triple product,
confinement, Q / gain…), promising reactor designs with results, engineering milestones
with measured substance (magnets, divertors, plasma-facing materials, tritium breeding,
heating, pellet injectors, diagnostics), and new physics regimes.

**Out of scope** — funding rounds, budgets, investment reports, political / governance
decisions, appointments, MoUs, conference announcements, awards, and pure PR with no
measured result behind it.

Each edition contains up to five story groups, ranked by significance. Every group has a
10–20 sentence summary (what the result is → the mechanism → the comparison to the prior
record → what it means for fusion), a full source list, and — whenever US, European, and
Asian sources frame the story differently — a paragraph analysing that difference.

## How it runs

The routine is not code in this repo; it is instruction text in **[`ROUTINE.md`](ROUTINE.md)**
that is pasted into a **Claude scheduled task**.

- **Schedule:** 1st of every month, **08:00 Europe/Zurich**.
- **Publish:** the task writes `YYYY/YYYY-MM.md` and prepends an entry to `index.md` via the
  GitHub contents API — most easily through the [`publish.sh`](publish.sh) helper.
- **Notify:** a two-tier push via [ntfy.sh](https://ntfy.sh) — each group's headline plus a
  one-line significance note, linking to the published report. Quiet months notify anyway,
  and any publish failure fires a `warning` alert so nothing fails silently.

## Repository layout

```
index.md          running list of all editions, newest first (rendered as the site home)
_config.yml        Jekyll config — Architect theme
2026/2026-08.md    one file per month
ROUTINE.md         the routine's full instruction text (paste into a scheduled task)
publish.sh         helper: publish a month report + update the index in one call
```

## Configuration

| What | Value |
|------|-------|
| ntfy server / topic | `ntfy.sh` / `fusion-frontier-k3n8vq2p` |
| GitHub Pages | Deploy from branch `main`, folder `/` (root) |
| Theme | `jekyll-theme-architect` |
| Publish auth | `GITHUB_TOKEN` env var (contents:write) used by `publish.sh` |

### `publish.sh`

```bash
export GITHUB_TOKEN=<token with contents:write>
./publish.sh <YYYY> <MM> report.md "<one-line teaser for the index>"
```

It fetches the existing file SHA where needed, writes the month report, prepends the index
entry, and sends an ntfy `warning` push if anything fails.
