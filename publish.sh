#!/usr/bin/env bash
#
# publish.sh — publish one monthly Fusion Digest report and update the index,
# committing straight to the repo with git. No token needed: the routine runs
# as a Claude session already authenticated to the repo via the GitHub App.
# Sends an ntfy alert if anything fails, so publishing never fails silently.
#
# Usage:
#   ./publish.sh <YYYY> <MM> <path-to-report.md> "<one-line index teaser>"
#
# Example:
#   ./publish.sh 2026 08 report.md "EAST breaks 1,066 s; W7-X sets triple-product record"
#
# Optional overrides:
#   BRANCH       default: main
#   NTFY_TOPIC   default: fusion-frontier-k3n8vq2p
#
set -euo pipefail

BRANCH="${BRANCH:-main}"
NTFY_TOPIC="${NTFY_TOPIC:-fusion-frontier-k3n8vq2p}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

notify_failure() {
  # Never fail silently — push an alert so a broken run is visible.
  curl -s -G "https://ntfy.sh/${NTFY_TOPIC}/trigger" \
    --data-urlencode "title=Fusion Digest — publish FAILED" \
    --data-urlencode "tags=warning" \
    --data-urlencode "message=$1" >/dev/null 2>&1 || true
}
fail() { echo "ERROR: $1" >&2; notify_failure "$1"; exit 1; }

# --- args & preconditions ----------------------------------------------------
[ "$#" -ge 3 ] || fail "usage: publish.sh <YYYY> <MM> <report.md> [teaser]"
YEAR="$1"; MONTH="$(printf '%02d' "$((10#$2))")"; REPORT="$3"; TEASER="${4:-See the report.}"
[ -f "$REPORT" ] || fail "report file not found: $REPORT"
command -v python3 >/dev/null 2>&1 || fail "python3 required"

MONTH_NAME="$(python3 -c "import datetime;print(datetime.date($YEAR,int('$MONTH'),1).strftime('%B %Y'))")"
MONTH_PATH="${YEAR}/${YEAR}-${MONTH}.md"

# --- 1. place the month report ----------------------------------------------
mkdir -p "$YEAR"
[ "$REPORT" -ef "$MONTH_PATH" ] || cp "$REPORT" "$MONTH_PATH"

# --- 2. prepend the index entry ---------------------------------------------
# Insert right after the marker; drop the empty-state placeholder once real
# entries exist. Newest edition ends up at the top of the list.
ENTRY="- [${MONTH_NAME}](${YEAR}/${YEAR}-${MONTH}.html) — ${TEASER}"
ENTRY="$ENTRY" python3 - <<'PY'
import os
entry  = os.environ["ENTRY"]
marker = "<!-- DIGESTS:START -->"
path   = "index.md"
lines  = open(path, encoding="utf-8").read().splitlines()
out, inserted = [], False
for line in lines:
    out.append(line)
    if not inserted and line.strip() == marker:
        out.append(entry); inserted = True
if not inserted:                    # marker missing — append as a fallback
    out.append(entry)
out = [l for l in out if l.strip() != "_No editions published yet._"]
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY

# --- 3. commit & push --------------------------------------------------------
git config user.email >/dev/null 2>&1 || git config user.email "fusion-digest@users.noreply.github.com"
git config user.name  >/dev/null 2>&1 || git config user.name  "Fusion Digest"

git add "$MONTH_PATH" index.md
if git diff --cached --quiet; then echo "Nothing to commit."; exit 0; fi
git commit -q -m "Publish Fusion Digest ${MONTH_NAME}" || fail "git commit failed"

# retry push on transient network failures (2s, 4s, 8s, 16s)
n=0
until git push origin "$BRANCH"; do
  n=$((n+1)); [ "$n" -ge 4 ] && fail "git push to ${BRANCH} failed after retries"
  sleep $((2 ** n))
done

echo "Done. Report: https://lukas-walker.github.io/fusion-digest/${YEAR}/${YEAR}-${MONTH}.html"
