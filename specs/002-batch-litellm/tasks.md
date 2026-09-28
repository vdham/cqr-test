# 002 — tasks

## Part 1 — commit `feat(api): async batch jobs with bounded in-process queue`
- [ ] `cqr/jobs.py`: `Job`, `JobError`, `JobRunner` (queue + N workers via `to_thread`, flush cadence, eviction, idempotency, circuit breaker)
- [ ] `schema.py`: `Review.job_id`, `BatchAccepted`, `CQR_MAX_BATCH` on `transcripts`
- [ ] `api.py`: lifespan wiring; `POST /review/batch` → 202; `?wait=true`; `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/reviews`; `/reviews?job_id=`; `Idempotency-Key`
- [ ] `cli.py`: `--concurrency`, exit 1 on failures, exit 2 on missing file, skip malformed JSONL lines
- [ ] Dashboard meta shows `job_id`
- [ ] `tests/test_jobs.py` + API/CLI test additions; coverage 100%

## Part 2 — commit `feat(judge): LiteLLM-backed judge with classified errors`
- [ ] `requirements.txt`: `litellm`; drop `anthropic` if unused
- [ ] `cqr/errors.py`: `JudgeError` + `JudgeUnavailable` (503) / `JudgeRejected` (502) / `TranscriptRejected` (422) / `JudgeOutputInvalid` (500)
- [ ] `LLMJudge` replaces `AnthropicJudge`; `_classify()`; separate transport vs output retries; no `fallbacks`; `api_base` from `CQR_LLM_BASE_URL`
- [ ] `get_judge`: `llm|heuristic`, `anthropic` alias
- [ ] `api.py`: `JudgeError` handler with `Retry-After`; `GET /health`
- [ ] `jobs.py`: per-item errors from `JudgeError`; config-scope error aborts job as `failed`
- [ ] `cli.py`: `JudgeRejected` → exit 3
- [ ] Tests with monkeypatched `litellm.completion`; coverage 100%

## Docs
- [ ] README: accept-then-poll flow, error contract, environment table, layout, test count
- [ ] TRADEOFFS: queue/concurrency paragraph; LiteLLM judge paragraph; corners cut; hardening

## Acceptance
- [ ] `PROMPT.md` acceptance checklist 1–5 pass
- [ ] `STATUS.md` updated
