# Changelog

## Unreleased
- (nothing queued)

## 0.3 — 2026-09-28
- specs/002-batch-litellm complete (two commits):
  - Part 1 `feat(api): async batch jobs with bounded in-process queue` (`0773603`): `cqr/jobs.py::JobRunner` with `asyncio.Queue` + N workers, latched per-job circuit breaker, LRU eviction, `Idempotency-Key` dedupe. Schema: `Job`, `JobStatus`, `JobError`, `BatchAccepted`, `Review.job_id`, `CQR_MAX_BATCH` cap. `POST /review/batch` is now 202+poll (or 200 with `?wait=true`), `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/reviews`, `/reviews?job_id=` filter. CLI: `--concurrency`, exit 1 on failures, exit 2 on missing file, malformed-JSONL tolerance. Dashboard surfaces `job_id`.
  - Part 2 `feat(judge): LiteLLM-backed judge with classified errors`: `cqr/errors.py` with the four `JudgeError` subclasses (`JudgeUnavailable`/`JudgeRejected`/`TranscriptRejected`/`JudgeOutputInvalid`). `LLMJudge` replaces `AnthropicJudge` via LiteLLM (no `fallbacks=`; `api_base` from `CQR_LLM_BASE_URL`); `_classify_litellm_error` maps every provider exception. API: exception handler with typed body and `Retry-After: 30` on 503; `GET /health`. JobRunner: config-scope errors abort the whole job; only retryable failures count toward the circuit. CLI: `JudgeRejected` → exit 3. Docs: README env table, error contract, accept-then-poll curl example; TRADEOFFS queue/LiteLLM paragraphs.

## 0.2 — 2026-09-28
- specs/001-fixes complete (§A–§E, commits `b7c8d5e`..`daee7b3`):
  - Contract: typed signal-level results, `_finalize` normalization for level typography, evidence-citation validation with `Review.warnings`, `RUBRIC_VERSION` stamped on every review, `Transcript` requires non-empty and unique+contiguous `idx`.
  - Loader: exact→alias→fuzzy resolution, `refund_status` and every kb.json slug now resolve correctly; kb.json wired for slug validation.
  - Rubric bumped to 1.1: correctness scoped to observable claims/actions, no-reference wording aligned with the invariant.
  - CLI `show --needs-review --source --json`; shared sort key with the API; `review --fresh`; eval script accepts `a|b` OR labels.
  - Dashboard escapes all model-controlled strings; shows `rubric_version` and per-review `warnings`.
  - Docs: `docs/ORIGINAL_PLAN.md` (superseded) replaces `docs/PLAN.md`; `docs/jev-analysis.md` removed; `examples/reviews.anthropic.json` committed (22/22 on synthetic set).
- Repo scaffold: `AGENTS.md`, `STATUS.md`, `specs/`, `docs/decisions/`, `CHANGELOG.md`, `CQR_FIXES.md`.

## 0.1 — 2026-09-27
- Initial prototype: five anchored signals, Anthropic + heuristic judges, ABCD + synthetic loaders, FastAPI + CLI + dashboard, 126 hermetic tests.
