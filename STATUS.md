# Status

_Last updated: 2026-09-28 — by: Vikram_

| Spec | Goal | State | Next task |
|---|---|---|---|
| [001-fixes](specs/001-fixes/tasks.md) | Close the review findings: contract enforcement, guideline lookup, rubric, docs | **done** (`b7c8d5e`..`daee7b3`) | — |
| [002-batch-litellm](specs/002-batch-litellm/tasks.md) | Async batch jobs with bounded queue; LiteLLM judge with classified errors; hardened error contract | **done** (`0773603`, `a94396a`, `2587866`) | — |
| [003-hardening](specs/003-hardening/tasks.md) | Port eight practices from sibling repo: CI evidence, licensing, README shape, timed demo, prompt caching, structured outputs, content-addressed identity, stale detection | **in progress** | A: CI workflow + badge |

## Done this session
- Runner lazy-starts on first `POST /review/batch` if lifespan hasn't; the 429 branch is now for real `QueueFull` only. Matrix and tests updated.
- README error table gains a **Client should check** column (via `scripts/render_error_table.py`); 422 rows spell out the body-shape branch.
- Anthropic run refreshed on rubric 1.1 with the LiteLLM judge — `examples/reviews.anthropic.json` now has `"judge": "llm:claude-sonnet-4-5"`. Score unchanged: **22/22**.
- `scripts/demo.sh` — one-shot demo (dashboard + interesting URLs + live batch through `/review/batch?wait=true`); works with no API key.
- `tests/fixtures/batch3.json` seed for the demo batch.
- README + TRADEOFFS interviewer read-through: test count refreshed to 315, JSON contract example updated to the current `judge`/`rubric_version`/`warnings` fields, env table adds `CQR_MAX_QUEUE_SIZE` and `CQR_MAX_BODY_BYTES`, layout section mentions demo.sh and the render script.
- Fresh-clone verification (`3a`) — see the report at the bottom of this file.

## Next
- No queued spec. Natural next candidate is a **003 — hardening pass** (broker + out-of-process workers, LiteLLM gateway policy, PII redaction with typed placeholders, ADRs for each). Draft `specs/003-hardening/{spec,tasks,PROMPT}.md` when starting.

## Blocked / open questions
- None.

## Demo readiness
- [x] `pytest` green (315 tests), coverage 100% on `cqr/*`
- [x] Heuristic path runs end-to-end with no key (`python -m cqr.cli review --synthetic ... --judge heuristic`)
- [x] `scripts/demo.sh` starts the API, prints URLs for `syn-06`/`syn-01`/`syn-05`, drives a batch through `POST /review/batch?wait=true`, all with no API key
- [x] `POST /review/batch` returns 202 with a `job_id`; `GET /jobs/{id}` and `/jobs/{id}/reviews` return the async result
- [x] `GET /health` reports which judge/model is live without touching the provider
- [x] `JudgeError` handler returns typed HTTP responses with `Retry-After: 30` on 503
- [x] Every non-2xx serializes to `ErrorBody` (except FastAPI's own 422), and `tests/test_error_contract.py` pins the spec against the code
- [x] Dashboard shows `syn-01` with `correctness=contradicted` on the committed LLM run (`CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app`)
- [x] `TRADEOFFS.md` has no contradictions with the code
- [x] Anthropic eval evidence in `examples/reviews.anthropic.json` (22/22, rubric 1.1, judge `llm:claude-sonnet-4-5`)
