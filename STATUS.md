# Status

_Last updated: 2026-09-28 — by: Vikram_

| Spec | Goal | State | Next task |
|---|---|---|---|
| [001-fixes](specs/001-fixes/tasks.md) | Close the review findings: contract enforcement, guideline lookup, rubric, docs | **done** (`b7c8d5e`..`daee7b3`) | — |
| [002-batch-litellm](specs/002-batch-litellm/tasks.md) | Async batch jobs with bounded queue; LiteLLM judge with classified errors | **Part 1 done**, Part 2 pending | Part 2: `LLMJudge` + `cqr/errors.py` + `/health` |

## Done this session
- Closed Part 1 of `specs/002-batch-litellm`: `cqr/jobs.py` with `JobRunner` (asyncio queue + workers via `to_thread`, latched circuit breaker, LRU eviction, idempotency), schema additions (`Job`, `JobStatus`, `JobError`, `BatchAccepted`, `Review.job_id`, `CQR_MAX_BATCH`), rewired `/review/batch` to 202+poll (with `?wait=true`), added `/jobs`, `/jobs/{id}`, `/jobs/{id}/reviews`, `/reviews?job_id=`, `Idempotency-Key`. CLI gets `--concurrency`, exit-1-on-failure, exit-2-on-missing-file, malformed-JSONL tolerance. Dashboard surfaces `job_id`. 263 hermetic tests, 100% coverage on `cqr/*`.

## Next
- Work `specs/002-batch-litellm/tasks.md` Part 2 (LiteLLM judge + classified errors + `/health` + `JudgeRejected` → CLI exit 3). Full instructions at `specs/002-batch-litellm/PROMPT.md`.

## Blocked / open questions
- None.

## Demo readiness
- [x] `pytest` green (229 tests), coverage 100% on `cqr/*`
- [x] Heuristic path runs end-to-end with no key (`python -m cqr.cli review --synthetic ... --judge heuristic`)
- [x] Dashboard shows `syn-01` with `correctness=contradicted` on the committed Anthropic run (`CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app`)
- [x] `TRADEOFFS.md` has no contradictions with the code
