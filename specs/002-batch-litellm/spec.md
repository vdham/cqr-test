# 002 — Async batch jobs and LiteLLM judge

**Why.** `POST /review/batch` runs LLM calls sequentially inside the request (20 transcripts ≈ minutes), with no concurrency bound, progress, or resubmission of failures. Provider errors are not classified; a bad key fails 25 times identically. Full design and decisions: the "async batch queue + LiteLLM judge" instruction file (kept alongside this spec as `PROMPT.md`).

**Depends on.** `specs/001-fixes` complete (typed levels, `_finalize` as the invariant layer, `rubric_version`).

**Scope.** Part 1: `cqr/jobs.py` in-process bounded queue, `202 + job_id`, `GET /jobs/{id}`, `GET /jobs/{id}/reviews`, `?wait=true`, idempotency key, circuit breaker; CLI `--concurrency`, exit codes, bad-line tolerance. Part 2: `LLMJudge` via LiteLLM, `cqr/errors.py` with four error types → HTTP status mapping, `/health`, job abort on config errors.

**Explicit non-goals.** Broker/Redis/Postgres; out-of-process workers; cross-model fallback (attribution); per-item input validation (whole-body 422 is the documented contract).

**Done when.** `tasks.md` ticked; acceptance checklist in `PROMPT.md` passes; two commits as named there.
