#!/usr/bin/env bash
#
# publish.sh — publish one monthly Fusion Digest report and update the index,
# via the GitHub contents API (curl over api.github.com), then alert on failure.
#
# Usage:
#   ./publish.sh <YYYY> <MM> <path-to-report.md> "<one-line index teaser>"
#
# Example:
#   ./publish.sh 2026 08 report.md "EAST breaks 1,066 s; W7-X sets triple-product record"
#
# Requires:
#   GITHUB_TOKEN   token with contents:write on the repo (Bearer auth)
# Optional overrides:
#   REPO           default: lukas-walker/fusion-digest
#   BRANCH         default: main
#   NTFY_TOPIC     default: fusion-frontier-k3n8vq2p
#
set -euo pipefail

REPO="${REPO:-lukas-walker/fusion-digest}"
BRANCH="${BRANCH:-main}"
NTFY_TOPIC="${NTFY_TOPIC:-fusion-frontier-k3n8vq2p}"
API="https://api.github.com/repos/${REPO}/contents"

notify_failure() {
  # Never fail silently — push an alert so an expired token or API error is visible.
  curl -s -G "https://ntfy.sh/${NTFY_TOPIC}/trigger" \
    --data-urlencode "title=Fusion Digest — publish FAILED" \
    --data-urlencode "tags=warning" \
    --data-urlencode "message=$1" >/dev/null 2>&1 || true
}

fail() { echo "ERROR: $1" >&2; notify_failure "$1"; exit 1; }

# --- args & preconditions ----------------------------------------------------
[ "$#" -ge 3 ] || fail "usage: publish.sh <YYYY> <MM> <report.md> [teaser]"
YEAR="$1"; MONTH="$2"; REPORT="$3"; TEASER="${4:-}"
MONTH="$(printf '%02d' "$((10#$MONTH))")"   # normalise to two digits

[ -n "${GITHUB_TOKEN:-}" ] || fail "GITHUB_TOKEN not set"
[ -f "$REPORT" ]           || fail "report file not found: $REPORT"
command -v python3 >/dev/null 2>&1 || fail "python3 required"

MONTH_PATH="${YEAR}/${YEAR}-${MONTH}.md"
MONTH_NAME="$(python3 -c "import datetime;print(datetime.date($YEAR,int('$MONTH'),1).strftime('%B %Y'))")"
[ -n "$TEASER" ] || TEASER="See the report."
INDEX_ENTRY="- [${MONTH_NAME}](${YEAR}/${YEAR}-${MONTH}.html) — ${TEASER}"

auth=(-H "Authorization: Bearer ${GITHUB_TOKEN}" -H "Accept: application/vnd.github+json")

# GET a file's JSON (may be a 404 body if absent).
gh_get() { curl -s "${auth[@]}" "${API}/$1?ref=${BRANCH}"; }

# PUT base64 content at a path. Args: path b64 message [sha]. Echoes HTTP code.
gh_put() {
  local path="$1" b64="$2" msg="$3" sha="${4:-}"
  local payload
  payload="$(SHA="$sha" MSG="$msg" B64="$b64" BR="$BRANCH" python3 - <<'PY'
import json, os
d = {"message": os.environ["MSG"], "content": os.environ["B64"], "branch": os.environ["BR"]}
if os.environ.get("SHA"): d["sha"] = os.environ["SHA"]
print(json.dumps(d))
PY
)"
  curl -s -o /dev/null -w '%{http_code}' -X PUT "${auth[@]}" "${API}/${path}" -d "$payload"
}

# Extract .sha from a contents-API JSON blob (empty if not a file / 404).
json_sha() { python3 -c "import sys,json
try: print(json.load(sys.stdin).get('sha','') or '')
except Exception: print('')"; }

# base64 (no newlines) of a local file.
b64_file() { python3 -c "import base64,sys;print(base64.b64encode(open(sys.argv[1],'rb').read()).decode())" "$1"; }

# --- 1. publish the month report --------------------------------------------
echo "Publishing ${MONTH_PATH} …"
SHA="$(gh_get "$MONTH_PATH" | json_sha)"
CODE="$(gh_put "$MONTH_PATH" "$(b64_file "$REPORT")" "Publish Fusion Digest ${MONTH_NAME}" "$SHA")"
case "$CODE" in 2*) : ;; *) fail "month file PUT ${MONTH_PATH} returned HTTP ${CODE}";; esac

# --- 2. prepend the index entry ---------------------------------------------
echo "Updating index.md …"
IDX_JSON="$(gh_get "index.md")"
IDX_SHA="$(printf '%s' "$IDX_JSON" | json_sha)"
[ -n "$IDX_SHA" ] || fail "could not read index.md (no sha)"

NEW_INDEX_B64="$(ENTRY="$INDEX_ENTRY" python3 - <<'PY'
import base64, json, os, sys
blob = json.load(sys.stdin)
text = base64.b64decode(blob["content"]).decode("utf-8")
entry = os.environ["ENTRY"]
marker = "<!-- DIGESTS:START -->"
lines = text.splitlines()
out, inserted = [], False
for line in lines:
    out.append(line)
    if not inserted and line.strip() == marker:
        out.append(entry)
        inserted = True
if not inserted:  # marker missing — fall back to appending under a heading
    out.append(entry)
# drop the empty-state placeholder once real entries exist
out = [l for l in out if l.strip() != "_No editions published yet._"]
print(base64.b64encode(("\n".join(out) + "\n").encode("utf-8")).decode())
PY
<<<"$IDX_JSON")"

CODE="$(gh_put "index.md" "$NEW_INDEX_B64" "Add ${MONTH_NAME} to index" "$IDX_SHA")"
case "$CODE" in 2*) : ;; *) fail "index.md PUT returned HTTP ${CODE}";; esac

echo "Done. Report: https://lukas-walker.github.io/fusion-digest/${YEAR}/${YEAR}-${MONTH}.html"
