# Conversation Quality Reviewer

Reads customer–agent transcripts, scores each on five anchored quality signals with rationale and turn citations, and exposes the results as an API, a CLI, and a small dashboard.

Signals (see `TRADEOFFS.md` for why):

| Tier | Signal | Output |
|---|---|---|
| 1 · must know | **risk_flags** | list of `{type, severity, rationale, turns}` — a gate, not a score |
| 1 | **resolution** | `resolved` · `partially_resolved` · `deferred_with_owner` · `unresolved` |
| 1 | **correctness** | `supported` · `contradicted` · `unverifiable` (vs. a reference policy) |
| 2 · explain | **customer_effort** | `low` · `medium` · `high` (high is bad) |
| 2 | **interaction_quality** | `low` · `medium` · `high` |
| context | sentiment_trajectory | per customer turn, −1…+1, plus `sentiment_delta` |

Every signal returns `{level, rationale, turns}`. Derived fields (`needs_human_review`, `sentiment_delta`, risk-flag deduping, trajectory sort) are enforced by the `Review` schema at construction — see [Invariants](#invariants) below.

## Setup

```bash
git clone https://github.com/vdham/cqr-test && cd cqr-test
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY; omit to fall back to the offline heuristic
```

The judge is LiteLLM-backed (`cqr/judge.py::LLMJudge`) so any provider LiteLLM speaks — Anthropic, OpenAI, Azure, an OpenAI-compatible gateway — works via one interface. Point `CQR_LLM_BASE_URL` at a gateway to override the endpoint.

Data: `data/abcd/` holds ABCD's `abcd_sample.json` (3 convos), `guidelines.json`, `kb.json`. For more ABCD conversations drop `abcd_v1.1.json.gz` from https://github.com/asappresearch/abcd into `data/abcd/` and the loader picks it up. `data/synthetic.jsonl` has nine deliberately problematic transcripts (regenerate with `python scripts/make_synthetic.py data/abcd`).

## Run

```bash
# batch review: synthetic set + the 3 shipped ABCD samples (up to 15 with the full
# dataset in data/abcd/) -> out/reviews.json
python -m cqr.cli review --synthetic data/synthetic.jsonl --abcd data/abcd --limit 15

# force the offline baseline (no API key needed)
python -m cqr.cli review --synthetic data/synthetic.jsonl --judge heuristic

# riskiest first
python -m cqr.cli show

# how did the judge do on the labeled synthetic set (offline; read only —
# does NOT call the model. Run `review` above first.)
python scripts/eval_synthetic.py

# API + dashboard
uvicorn cqr.api:app --reload      # dashboard http://127.0.0.1:8000/  ·  Swagger UI http://127.0.0.1:8000/docs (prefilled example bodies)
```

### Batch flow (accept-then-poll)

`POST /review/batch` is asynchronous: it returns 202 with a `job_id`, workers score transcripts concurrently, and callers poll `/jobs/{id}` (or hit the same endpoint with `?wait=true` to block up to `CQR_BATCH_WAIT_S`).

```bash
# 1. submit — 202 immediately
JOB=$(curl -sX POST http://127.0.0.1:8000/review/batch \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: my-batch-2026-09-28-01' \
  -d '{"transcripts": [{"id":"c1","source":"upload","turns":[{"idx":0,"speaker":"agent","text":"Hi"},{"idx":1,"speaker":"customer","text":"?"}]}]}' \
  | jq -r .job_id)

# 2. poll — job status + per-item errors
curl -s http://127.0.0.1:8000/jobs/$JOB
# 3. reviews it produced, riskiest first
curl -s http://127.0.0.1:8000/jobs/$JOB/reviews
```

### Environment

| Var | Default | Effect |
|---|---|---|
| `CQR_JUDGE` | (auto) | `llm` \| `heuristic`. `anthropic` accepted as back-compat alias for `llm`. If unset: `llm` when any of `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `CQR_LLM_BASE_URL` is set, else `heuristic`. |
| `CQR_MODEL` | `claude-sonnet-4-5` | LiteLLM model string; anything LiteLLM speaks works (`gpt-4o`, `openrouter/...`, `azure/...`, etc.). |
| `CQR_LLM_BASE_URL` | — | Pass through to `litellm.completion(api_base=...)`. Use to route through a gateway. |
| `CQR_LLM_TIMEOUT_S` | `60` | Per-call timeout at the LiteLLM boundary. |
| `CQR_LLM_RETRIES` | `2` | LiteLLM transport retries (connection resets, 5xx). Separate from output retries. |
| `CQR_CONCURRENCY` | `4` | Number of workers in the async batch queue. |
| `CQR_MAX_BATCH` | `200` | Cap on `transcripts[]` per `POST /review/batch`. Oversize → 422. |
| `CQR_BATCH_WAIT_S` | `60` | Max seconds `POST /review/batch?wait=true` blocks before returning the current job snapshot. |
| `CQR_CIRCUIT_THRESHOLD` | `5` | Consecutive retryable failures on one job before remaining items short-circuit as `CircuitOpen`. |
| `CQR_MAX_JOBS_KEPT` | `100` | LRU cap on the in-memory job table. |
| `CQR_STORE` | `out/reviews.json` | Path to the JSON store. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest                          # 292 tests, ~4s, no network
coverage run --source=cqr -m pytest && coverage report   # 100% line coverage across cqr/*
```

Tests are pure-Python and hermetic: `litellm.completion` is monkeypatched, the store uses `tmp_path`, and the FastAPI endpoints run through `TestClient` against a `HeuristicJudge`. `scripts/eval_synthetic.py` is separate — it scores stored reviews against `metadata.expect`, offline (no model call). Run `python -m cqr.cli review --synthetic data/synthetic.jsonl ...` first.

Synthetic-set eval scores (nine hand-labeled conversations, 22 anchored checks):

| Judge | Model | Rubric | Eval |
|---|---|---|---|
| `heuristic` | (regex baseline) | 1.1 | 18/22 |
| `anthropic` | `claude-sonnet-4-5` | 1.1 | 22/22 |

The anthropic run is committed at `examples/reviews.anthropic.json`. Browse it in the dashboard with no key needed:

```bash
CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app --reload
```

## Contract

Input is a `Transcript`; output is a `Review`. Both are Pydantic models in `cqr/schema.py`, and FastAPI publishes the OpenAPI spec at `/docs`.

Callable directly from Python — the API and CLI are wrappers around this two-line contract:

```python
from cqr.judge import get_judge
from cqr.schema import Transcript, Turn
review = get_judge().judge(Transcript(id="c1", source="upload",
                                      turns=[Turn(speaker="agent", text="Hi"),
                                             Turn(speaker="customer", text="?")]))
review.model_dump()
```

```jsonc
// POST /review
{
  "id": "conv-123",
  "source": "upload",
  "intent": "shipping_issue/missing",          // optional
  "reference": "AGENT GUIDELINES: ...",        // optional; without it correctness = unverifiable
  "turns": [
    {"idx": 0, "speaker": "agent",    "text": "Hi, how can I help?"},
    {"idx": 1, "speaker": "customer", "text": "My package never came..."}
  ]
}
```

```jsonc
// -> Review
{
  "transcript_id": "conv-123",
  "risk_flags": [{"type": "unauthorized_promise", "severity": "high", "rationale": "...", "turns": [4]}],
  "resolution":          {"level": "resolved",     "rationale": "...", "turns": [4, 6]},
  "correctness":         {"level": "contradicted", "rationale": "...", "turns": [4]},
  "customer_effort":     {"level": "low",          "rationale": "...", "turns": []},
  "interaction_quality": {"level": "medium",       "rationale": "...", "turns": [2]},
  "sentiment_trajectory": [{"turn": 1, "score": -0.4}, {"turn": 7, "score": 0.8}],
  "sentiment_delta": 1.2,
  "needs_human_review": true,
  "summary": "Reshipped at 3 days against the 7-day rule; customer happy, policy violated.",
  "judge": "anthropic:claude-sonnet-4-5"
}
```

Other endpoints:
- `POST /review/batch` `{transcripts: [...]}` — accepts up to `CQR_MAX_BATCH`, returns 202 with `{job_id, status, total}`. Use `?wait=true` to block up to `CQR_BATCH_WAIT_S` and get the completed `Job` back. `Idempotency-Key` header dedupes replays. See the accept-then-poll example under Run.
- `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/reviews` — job state, per-item errors, and the resulting reviews (riskiest first).
- `GET /reviews?needs_human_review=&source=&job_id=` — filter the persisted set.
- `GET /reviews/{id}` — transcript + review pair.
- `GET /health` — liveness (no provider call). Reports which judge is live and, for LLM, which model.

### Invariants

Any `Review` — however it was constructed (judge output, API request, test fixture) — satisfies these guarantees, enforced by a `@model_validator` in `cqr/schema.py`:

- **`risk_flags`**: exactly one entry per `(type, severity)` pair. Duplicates collapse; turns aggregate and sort; rationales dedupe-and-join with `" | "`. Output is sorted by severity desc, then type asc.
- **`sentiment_trajectory`**: sorted by turn ascending.
- **`sentiment_delta`**: derived as `last.score − first.score` rounded to 2dp (`0.0` when fewer than two points).
- **`needs_human_review`**: `true` iff any risk flag is `medium` or `high`, OR `correctness == contradicted`.
- **`correctness`**: forced to `unverifiable` at judge time when the transcript has no `reference` (this one lives in `judge._finalize` since it depends on external context, not the review alone).

Callers should never compute the derived fields themselves — supplied values are overwritten.

### Error contract

Judge failures are classified into four typed subclasses of `JudgeError` (`cqr/errors.py`). The API's exception handler maps each to an HTTP response `{error_type, message, retryable, attempts}`; 5xx-retryable responses carry `Retry-After: 30`.

| Class | HTTP | Scope | Retryable | Meaning |
|---|---|---|---|---|
| `JudgeUnavailable` | 503 | transcript | ✓ | Transient upstream failure (timeout, rate limit, connection drop, provider 5xx). Same input has a real chance of working later. |
| `JudgeRejected` | 502 | **config** | ✗ | Bad key, wrong model, permission denied. Every remaining item in the same batch would fail identically → the JobRunner aborts the whole job; the CLI exits 3. |
| `TranscriptRejected` | 422 | transcript | ✗ | Provider accepted the request but rejected this specific transcript (too long, content policy). One-item quarantine — other batch items proceed. |
| `JudgeOutputInvalid` | 500 | transcript | ✗ | Provider replied, but the JSON didn't validate against `Review` even after output retries. |

`POST /review` is **idempotent by `id`**: reposting the same transcript id replaces both the stored transcript and its review.

`POST /review/batch` is async (accept-then-poll) with an in-process bounded queue and per-job latched circuit breaker. Per-item failures land in `Job.errors` with the classified `error_type`, `retryable`, and `attempts` fields; the batch keeps running unless a `JudgeRejected` flips the whole job to `failed`.

### What's stable

The `Transcript` and `Review` schemas are the public contract. Judge implementations, rubric wording, dashboard HTML, storage layout, and CLI flag names are internal and may change.

## Layout

```
cqr/schema.py      the contract (Transcript in, Review out) + Job / JobStatus / JobError / BatchAccepted
cqr/rubric.py      the anchored rubric — this is the product
cqr/errors.py      the JudgeError taxonomy (Unavailable / Rejected / TranscriptRejected / OutputInvalid)
cqr/judge.py       LLMJudge (LiteLLM) and HeuristicJudge (regex baseline); both emit Review
cqr/jobs.py        in-process bounded queue: JobRunner, workers, circuit breaker, idempotency
cqr/loader.py      ABCD + JSONL ingest; maps ABCD intents to guideline text for correctness
cqr/cli.py         batch runner
cqr/api.py         FastAPI endpoints + dashboard
cqr/static/        one-file dashboard
scripts/           synthetic data generator, synthetic eval
tests/             pytest suite — hermetic, 100% line coverage across cqr/*
```
