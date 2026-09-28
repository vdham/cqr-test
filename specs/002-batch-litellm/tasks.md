# 002 — tasks

## Part 1 — commit `feat(api): async batch jobs with bounded in-process queue`
- [x] `cqr/jobs.py`: `JobRunner` (queue + N workers via `to_thread`, flush cadence, LRU eviction, idempotency, latched circuit breaker)
- [x] `schema.py`: `Review.job_id`, `BatchAccepted`, `Job`, `JobStatus`, `JobError`, `CQR_MAX_BATCH` on `transcripts`
- [x] `api.py`: lifespan wiring; `POST /review/batch` → 202; `?wait=true`; `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/reviews`; `/reviews?job_id=`; `Idempotency-Key`
- [x] `cli.py`: `--concurrency`, exit 1 on failures, exit 2 on missing file, skip malformed JSONL lines
- [x] Dashboard meta shows `job_id`
- [x] `tests/test_jobs.py` + API/CLI test additions; coverage 100% (263 tests)

## Part 2 — commit `feat(judge): LiteLLM-backed judge with classified errors`  → `<pending>`
- [x] `requirements.txt`: `litellm>=1.50`; dropped `anthropic` (LiteLLM talks to Anthropic directly)
- [x] `cqr/errors.py`: `JudgeError` + `JudgeUnavailable` (503) / `JudgeRejected` (502) / `TranscriptRejected` (422) / `JudgeOutputInvalid` (500)
- [x] `LLMJudge` replaces `AnthropicJudge`; `_classify_litellm_error`; separate transport vs output retries; no `fallbacks`; `api_base` from `CQR_LLM_BASE_URL`
- [x] `get_judge`: `llm|heuristic`, `anthropic` alias
- [x] `api.py`: `JudgeError` handler with `Retry-After`; `GET /health`
- [x] `jobs.py`: per-item errors from `JudgeError`; config-scope error aborts job as `failed`; only retryable failures count toward the circuit
- [x] `cli.py`: `JudgeRejected` → exit 3
- [x] Tests with monkeypatched `litellm.completion`; coverage 100% (292 tests total)

## Docs
- [x] README: accept-then-poll flow (curl example), error contract table, environment table, layout, test count
- [x] TRADEOFFS: queue/concurrency paragraph; LiteLLM judge paragraph; corners cut (in-memory jobs, in-process workers, whole-body 422); hardening (broker + out-of-process workers, gateway policy)

## Acceptance
- [x] `PROMPT.md` acceptance checklist 1–5 pass
- [x] `STATUS.md` updated
