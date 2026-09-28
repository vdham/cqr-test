# Status

_Last updated: 2026-09-28 — by: Vikram_

| Spec | Goal | State | Next task |
|---|---|---|---|
| [001-fixes](specs/001-fixes/tasks.md) | Close the review findings: contract enforcement, guideline lookup, rubric, docs | **done** (`b7c8d5e`..`daee7b3`) | — |
| [002-batch-litellm](specs/002-batch-litellm/tasks.md) | Async batch jobs with bounded queue; LiteLLM judge with classified errors | **done** | — |

## Done this session
- Closed Part 2 of `specs/002-batch-litellm`: `cqr/errors.py` (`JudgeError` taxonomy — `JudgeUnavailable`/`JudgeRejected`/`TranscriptRejected`/`JudgeOutputInvalid`), replaced `AnthropicJudge` with `LLMJudge` (LiteLLM, `fallbacks=` deliberately off, `_classify_litellm_error` maps every provider exception to a `JudgeError` subclass, separate transport vs output retries). Wired `JudgeError` exception handler and `GET /health` in `api.py`; the `JobRunner` now consumes `JudgeError.scope=="config"` to abort a job as `failed`, and only retryable failures count toward the circuit. CLI exits 3 on `JudgeRejected`. Requirements shift: `litellm>=1.50` in, `anthropic` dropped. Docs updated: README env table, error contract, accept-then-poll flow; TRADEOFFS queue/LLM paragraphs plus corners-cut and hardening lines. 292 hermetic tests, 100% coverage on `cqr/*`.
- Part 1 also landed earlier in this session (`0773603`): `cqr/jobs.py` (`JobRunner`), `Job`/`JobStatus`/`JobError`/`BatchAccepted`, `Review.job_id`, `POST /review/batch` → 202+poll, `/jobs*` endpoints, `Idempotency-Key`, CLI `--concurrency` + exit codes + malformed-line tolerance, dashboard `job_id`.

## Next
- No queued spec. Next candidate: **003 — hardening pass** (broker/Postgres queue + out-of-process workers, LiteLLM gateway policy, PII redaction with typed placeholders, ADRs for each). Draft `specs/003-hardening/spec.md + tasks.md + PROMPT.md` when starting.

## Blocked / open questions
- None.

## Demo readiness
- [x] `pytest` green (292 tests), coverage 100% on `cqr/*`
- [x] Heuristic path runs end-to-end with no key (`python -m cqr.cli review --synthetic ... --judge heuristic`)
- [x] `POST /review/batch` returns 202 with a `job_id`; `GET /jobs/{id}` and `/jobs/{id}/reviews` return the async result
- [x] `GET /health` reports which judge/model is live without touching the provider
- [x] `JudgeError` handler returns typed HTTP responses with `Retry-After: 30` on 503
- [x] Dashboard shows `syn-01` with `correctness=contradicted` on the committed Anthropic run (`CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app`)
- [x] `TRADEOFFS.md` has no contradictions with the code
