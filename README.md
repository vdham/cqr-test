# Conversation Quality Reviewer

[![CI](https://github.com/vdham/cqr-test/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/vdham/cqr-test/actions/workflows/ci.yml)

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

## Reading order

Depending on why you're here:

| If you want to… | Read |
|---|---|
| Watch the four-minute walkthrough | [`specs/DEMO.md`](specs/DEMO.md) |
| See what the prototype proves and where it stops | [Deliberate scope](#deliberate-scope) below · [`TRADEOFFS.md`](TRADEOFFS.md) |
| Understand the current state of work | [`STATUS.md`](STATUS.md) |
| Follow the working process | [`AGENTS.md`](AGENTS.md) · [`specs/`](specs) |
| See the design decisions the panel will ask about | [`docs/decisions/`](docs/decisions) (ADRs) |
| Read the full fix list this repo closed | [`CQR_FIXES.md`](CQR_FIXES.md) |
| Read the contract | [`cqr/schema.py`](cqr/schema.py) |
| Read the rubric (this is the product) | [`cqr/rubric.py`](cqr/rubric.py) |

## Setup

```bash
git clone https://github.com/vdham/cqr-test && cd cqr-test
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # or OPENAI_API_KEY; omit to fall back to the offline heuristic
```

The judge is LiteLLM-backed (`cqr/judge.py::LLMJudge`) so any provider LiteLLM speaks — Anthropic, OpenAI, Azure, an OpenAI-compatible gateway — works via one interface. Point `CQR_LLM_BASE_URL` at a gateway to override the endpoint.

Data: `data/abcd/` holds ABCD's `abcd_sample.json` (3 convos), `guidelines.json`, `kb.json`. For more ABCD conversations drop `abcd_v1.1.json.gz` from https://github.com/asappresearch/abcd into `data/abcd/` and the loader picks it up. `data/synthetic.jsonl` has nine deliberately problematic transcripts (regenerate with `python scripts/make_synthetic.py data/abcd`).

Third-party redistributions and their licenses are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

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

# one-shot demo: dashboard + interesting URLs + a live batch through the async job flow. Works with no API key.
scripts/demo.sh
```

### Expected output — `scripts/demo.sh`

Real output from a clean heuristic run, trimmed for brevity. The dashboard URLs are live for the ~2 minutes the demo runs.

```
==> demo store (scratch copy of examples/reviews.anthropic.json): out/demo-store.json
==> starting API on port 8000 (store=out/demo-store.json)

==> dashboard:                 http://127.0.0.1:8000/
==> OpenAPI (interactive):     http://127.0.0.1:8000/docs
==> health:                    http://127.0.0.1:8000/health

Three synthetic conversations worth opening in the dashboard:
  * http://127.0.0.1:8000/#/reviews/syn-06-exemplary             (good, everything supported)
  * http://127.0.0.1:8000/#/reviews/syn-01-wrong-but-happy       (happy customer, wrong answer)
  * http://127.0.0.1:8000/#/reviews/syn-05-pii                   (agent asks for CVV)

==> POST /review/batch?wait=true  (judge=heuristic)  <-  tests/fixtures/batch3.json
   job_id     = <uuid>
   status     = completed  (3/3)

==> reviews from that job (riskiest first):
 ! demo-batch-b        res=unresolved corr=unverifiable   flags=churn_signal
 ! demo-batch-c        res=unresolved corr=unverifiable   flags=pii_mishandling
   demo-batch-a        res=unresolved corr=unverifiable   flags=-
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
| `CQR_MAX_QUEUE_SIZE` | `0` (unbounded) | Bound the number of items in-flight across all jobs. If reached, `POST /review/batch` returns 429 QueueFull. |
| `CQR_MAX_BODY_BYTES` | `5000000` | Reject requests over this size at the ASGI layer with 413 PayloadTooLarge before any body parsing. |
| `CQR_STORE` | `out/reviews.json` | Path to the JSON store. |

## Tests

```bash
pip install -r requirements-dev.txt
pytest                          # 315 tests, ~4s, no network
coverage run --source=cqr -m pytest && coverage report   # 100% line coverage across cqr/*
```

Tests are pure-Python and hermetic: `litellm.completion` is monkeypatched, the store uses `tmp_path`, and the FastAPI endpoints run through `TestClient` against a `HeuristicJudge`. A drift-tracking ASGI middleware (see `tests/conftest.py`) records every `(route, status)` the app emits during the session; `tests/test_error_contract.py::TestDriftCheck` asserts none was undeclared in the OpenAPI spec. `scripts/eval_synthetic.py` is separate — it scores stored reviews against `metadata.expect`, offline (no model call). Run `python -m cqr.cli review --synthetic data/synthetic.jsonl ...` first.

Synthetic-set eval scores (nine hand-labeled conversations, 22 anchored checks):

| Judge | Model | Rubric | Eval |
|---|---|---|---|
| `heuristic` | (regex baseline) | 1.3 | 18/22 |
| `llm` | `claude-sonnet-4-5` (via LiteLLM) | 1.3 | 21/22 |

The one miss on the LLM run is `syn-03` interaction_quality — the model scored `medium` where the label calls for `low`. A single-run drift on one edge case; not a regression.

### Cost — measured, not modelled

Every `Review` from the LLM judge carries `usage` (input/output/cache tokens + `cost_usd`); every `Job` carries the per-batch sum. Numbers from the same 9-conversation run committed at `examples/reviews.anthropic.json`:

| Scenario | Cost (9 convos) | Note |
|---|---:|---|
| First run, prompt caching ON *(the committed run)* | **$0.074** | 25,256 of 31,632 input tokens (**80%**) served from the ephemeral cache |
| First run, prompt caching OFF | $0.139 | what a naïve implementation costs |
| Re-run the same batch, content-cache ON *(the default)* | **$0.00** | `Store.find` returns the stored review, judge is not called |
| Re-run with `--force` / `?force=true` | $0.139 | bypasses `Store.find`; full re-score |

The two mechanisms compound: (E) marks the system+rubric+reference block `cache_control: ephemeral` and enqueues batches in `(intent, id)` order so that prefix stays identical across consecutive calls — the same-intent runs are the cache-hit ones. (G) refuses to re-score a transcript when nothing content-relevant has changed. Together they mean the marginal cost of a fresh batch drops by roughly half and a re-run is free until the rubric or the reference actually changes — which (H)'s stale detection catches, so you know when to `--force`.

TRADEOFFS §Cost has the arithmetic and the pricing table.

The LLM run is committed at `examples/reviews.anthropic.json` (filename is legacy; the `judge` field on each review is `llm:claude-sonnet-4-5`). Browse it in the dashboard with no key needed:

```bash
CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app --reload
```

## Deliberate scope

**What this prototype proves.** The five signals and their anchored levels are a defensible choice — every signal answers a different question a supervisor asks, every judgment cites specific turns, and derived fields are enforced by the schema so a caller cannot supply a wrong `needs_human_review`. There's a working offline path (heuristic judge, regex-shaped, deliberately dumb) that exercises the whole pipeline without a key, and a real LiteLLM-backed path that scores 22/22 on the nine synthetic cases. Every risk-flag verdict is auditable via turn citations rather than opaque scores.

**Where it stops.** Judge accuracy is measured only against nine hand-labeled cases — a regression seed, not a benchmark. Reference lookup assumes a known intent (ABCD's `flow/subflow` labels); production would need intent classification + KB retrieval. The job runner is single-process and in-memory; a real deployment moves jobs to a broker and workers out of process. No auth, no PII redaction, no rate limiting — those belong in a LiteLLM proxy in front of this service. The `TRADEOFFS.md` file lists these under "What I'd harden" with one-line reasons.

### Design principles

1. **No composite score.** A gate can't be averaged — one PII flag surfaces the conversation for a human today regardless of how well it scored elsewhere. ADR: [`docs/decisions/0001-no-composite-score.md`](docs/decisions/0001-no-composite-score.md).
2. **Model proposes, code enforces.** Every derived field (`needs_human_review`, `sentiment_delta`, risk-flag dedupe, trajectory sort) is recomputed in the schema; nothing the model says can override them. Pinned by [`tests/test_invariants.py`](tests/test_invariants.py).
3. **A Review is complete or absent.** No partial reviews — Pydantic validation is total, and the retry loop is the way a "not yet complete" review becomes complete. Errors are typed (`ErrorBody`), never a half-filled shape.
4. **One model + one rubric version per review.** No cross-model fallback (attribution matters more than availability). `Review.judge` and `Review.rubric_version` are stamped on every review; a rubric edit that shifts scores is auditable. ADR: [`docs/decisions/0003-no-cross-model-fallback.md`](docs/decisions/0003-no-cross-model-fallback.md).
5. **Agents consume reviews, humans own the rubric.** The rubric — the anchored definitions of "supported" vs "contradicted" — is versioned text, not code. It's the artifact reviewers argue about; the code just serves it.

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
  "judge": "llm:claude-sonnet-4-5",
  "rubric_version": "1.1",
  "warnings": []
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

Callers should never compute the derived fields themselves — supplied values are overwritten. Verified by [`tests/test_invariants.py`](tests/test_invariants.py): no value the model emits for the derived or versioning fields (`needs_human_review`, `sentiment_delta`, `correctness` when no reference, `schema_version`, `rubric_version`, `transcript_digest`, `reference_version`) can change the computed one.

### Error contract

The section below is **generated** from `cqr.api.ERROR_RESPONSES` by `scripts/render_error_table.py`. Do not hand-edit between the markers; regenerate with `python scripts/render_error_table.py`. A test (`test_render_matches_readme`) pins the pasted text byte-for-byte.

<!-- ERROR-TABLE-START -->
### Status × error_type

| Status | `error_type` | Retryable | Meaning | Client should check | Client action |
|---|---|---|---|---|---|
| **404** | `NotFound` | ✗ | No resource with that id (may have been LRU-evicted from the job table). | — | Check the id; if it's a job, it may have aged out of `CQR_MAX_JOBS_KEPT`. |
| **413** | `PayloadTooLarge` | ✗ | Request body exceeded `CQR_MAX_BODY_BYTES`. | — | Split the batch or trim the transcript. |
| **422** | `HTTPValidationError` | ✗ | Request body failed schema validation (FastAPI's native `{detail: [...]}` shape). | Body has `detail: [...]` (no `error_type`). | Fix the request body and resend. |
| **422** | `TranscriptRejected` | ✗ | Provider accepted the request but rejected this transcript (too long, content policy). | Body is `ErrorBody` with `error_type == "TranscriptRejected"`. | Send the transcript to human review, or trim/redact and resubmit. |
| **429** | `QueueFull` | ✓ | In-process job queue at capacity. | `Retry-After` header. | Back off `Retry-After` seconds and resubmit. |
| **500** | `JudgeOutputInvalid` | ✗ | Provider replied, but JSON never validated after output retries. | — | Send the transcript to human review; the model can't self-correct. |
| **502** | `JudgeRejected` | ✗ | Provider refused authoritatively — bad key, wrong model, permission denied. | — | Fix credentials/config and rerun; batch aborts, CLI exits 3. |
| **503** | `JudgeUnavailable` | ✓ | Transient upstream failure — timeout, rate limit, connection drop. | `Retry-After` header. | Retry after `Retry-After` seconds; resubmit `errors[].transcript_id` where `retryable`. |

### Body shape

Every non-2xx serializes to `ErrorBody`, **except** FastAPI's own 422 for body validation, which keeps its native shape. Callers on 422 must branch on the JSON shape.

```jsonc
// ErrorBody — used by 404, 413, 422 (TranscriptRejected), 429, 500, 502, 503
{
  "error_type": "JudgeUnavailable",
  "message": "RateLimitError: 429 from provider",
  "retryable": true,
  "attempts": 2,
  "retry_after_s": 30
}
```

```jsonc
// FastAPI's native 422 (body validation) — different shape
{
  "detail": [
    {"loc": ["body", "turns"], "msg": "list should have at least 1 item", "type": "too_short"}
  ]
}
```

### Job semantics

- `status: completed` may still carry per-item `errors[]`. The batch as a whole is done; check each entry's `error_type` and `retryable` to decide what to resubmit.
- `status: failed` means a **config-scope** error (JudgeRejected) aborted the job — every remaining item would fail identically. Fix the config, then resubmit the batch fresh.
- `error_type: CircuitOpen` items are marked non-retryable in the error record itself, but the underlying failures were retryable — the circuit latched after `CQR_CIRCUIT_THRESHOLD` consecutive retryable failures. Resubmit them once the upstream is healthy.
- To resubmit only what failed:

  ```bash
  # ids of retryable per-item failures from a completed job
  curl -s http://127.0.0.1:8000/jobs/$JOB \
    | jq -r '.errors[] | select(.retryable) | .transcript_id'
  ```
<!-- ERROR-TABLE-END -->

`POST /review` is **idempotent by `id`**: reposting the same transcript id replaces both the stored transcript and its review.

### CLI exit codes

| Exit | Meaning |
|---|---|
| **0** | All transcripts scored successfully. |
| **1** | One or more per-transcript failures (see stderr for details). |
| **2** | An input file/directory was missing or unreadable. |
| **3** | `JudgeRejected` — config-scope failure (bad key, wrong model). Run aborted, no retry. |

### What's stable

The `Transcript` and `Review` schemas are the public contract. Judge implementations, rubric wording, dashboard HTML, storage layout, and CLI flag names are internal and may change.

## Layout

```
cqr/schema.py      the contract (Transcript in, Review out) + Job / JobStatus / JobError / BatchAccepted / ErrorBody
cqr/rubric.py      the anchored rubric — this is the product
cqr/errors.py      the JudgeError taxonomy + non-judge errors (QueueFull / NotFoundError / PayloadTooLarge)
cqr/judge.py       LLMJudge (LiteLLM) and HeuristicJudge (regex baseline); both emit Review
cqr/jobs.py        in-process bounded queue: JobRunner, workers, circuit breaker, idempotency
cqr/loader.py      ABCD + JSONL ingest; maps ABCD intents to guideline text for correctness
cqr/cli.py         batch runner
cqr/api.py         FastAPI endpoints + dashboard; single ERROR_RESPONSES matrix drives OpenAPI
cqr/static/        one-file dashboard
scripts/           synthetic data generator, synthetic eval, error-table renderer, one-shot demo (demo.sh)
tests/             pytest suite — hermetic, 100% line coverage across cqr/*; test_error_contract.py pins the OpenAPI spec against the code
```
