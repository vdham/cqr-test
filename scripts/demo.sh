#!/usr/bin/env bash
# One-shot demo: start the API, print the interesting URLs, drive a batch.
# Works with no API key (falls back to the heuristic judge and out/reviews.json).
#
#   scripts/demo.sh                  # background-start uvicorn, run, tear down
#   scripts/demo.sh --no-serve       # skip serve; assume already running
#
# Env you can override:
#   PORT       (default 8000)
#   CQR_JUDGE  (default: llm if a key is set, else heuristic)
set -euo pipefail

PORT="${PORT:-8000}"
BASE="http://127.0.0.1:${PORT}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# --- 1. pick a review store the dashboard should show ---------------------------
if [ -f "examples/reviews.anthropic.json" ]; then
  STORE="examples/reviews.anthropic.json"
  echo "==> using committed LLM eval as the demo store: $STORE"
else
  STORE="out/reviews.json"
  echo "==> no examples/reviews.anthropic.json; running heuristic judge to seed $STORE"
  mkdir -p out
  python -m cqr.cli review --synthetic data/synthetic.jsonl --judge heuristic --out "$STORE" --fresh >/dev/null 2>&1
fi
export CQR_STORE="$STORE"

# --- 2. start the API (unless --no-serve) --------------------------------------
UVICORN_PID=""
if [ "${1:-}" != "--no-serve" ]; then
  echo "==> starting API on port $PORT (store=$STORE)"
  uvicorn cqr.api:app --port "$PORT" --log-level warning >/tmp/cqr-demo.log 2>&1 &
  UVICORN_PID=$!
  trap 'if [ -n "$UVICORN_PID" ]; then kill "$UVICORN_PID" 2>/dev/null || true; fi' EXIT INT TERM
  # wait for /health
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -fs "$BASE/health" >/dev/null 2>&1; then break; fi
    sleep 0.3
  done
fi

# --- 3. interesting URLs -------------------------------------------------------
echo
echo "==> dashboard:                 $BASE/"
echo "==> OpenAPI (interactive):     $BASE/docs"
echo "==> health:                    $BASE/health"
echo
echo "Three synthetic conversations worth opening in the dashboard:"
echo "  * $BASE/#/reviews/syn-06-exemplary             (good, everything supported)"
echo "  * $BASE/#/reviews/syn-01-wrong-but-happy       (happy customer, wrong answer)"
echo "  * $BASE/#/reviews/syn-05-pii                   (agent asks for CVV)"
echo
echo "Raw review JSON for those three:"
for id in syn-06-exemplary syn-01-wrong-but-happy syn-05-pii; do
  if curl -fs "$BASE/reviews/$id" >/dev/null 2>&1; then
    echo "  $BASE/reviews/$id"
  fi
done

# --- 4. drive a batch through the async job flow -------------------------------
echo
echo "==> POST /review/batch?wait=true against tests/fixtures/batch3.json ..."
RESP=$(curl -sX POST "$BASE/review/batch?wait=true" \
  -H 'Content-Type: application/json' \
  --data-binary @tests/fixtures/batch3.json)
echo "$RESP" | python -c "
import json, sys
job = json.loads(sys.stdin.read())
print('   job_id     =', job['id'])
print('   status     = {}  ({}/{})'.format(job['status'], job['completed'], job['total']))
if job.get('errors'):
    print('   errors     =', [(e['transcript_id'], e['error_type']) for e in job['errors']])
"
JOB_ID=$(echo "$RESP" | python -c "import json,sys; print(json.loads(sys.stdin.read())['id'])")
echo
echo "==> reviews from that job (riskiest first):"
curl -s "$BASE/jobs/$JOB_ID/reviews" | python -c "
import json, sys
for r in json.loads(sys.stdin.read()):
    flag = '!' if r['needs_human_review'] else ' '
    flags = ','.join(f['type'] for f in r['risk_flags']) or '-'
    print(' {} {:18s}  res={:10s} corr={:14s} flags={}'.format(
        flag, r['transcript_id'], r['resolution']['level'], r['correctness']['level'], flags))
"

if [ "${1:-}" != "--no-serve" ]; then
  echo
  echo "==> demo complete. Press Ctrl-C to stop, or the trap will kill uvicorn on exit."
  wait "$UVICORN_PID"
fi
