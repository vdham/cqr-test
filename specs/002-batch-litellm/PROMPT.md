# 002 — Claude Code prompt: async batch queue + LiteLLM judge (`cqr-test`)

Paste into Claude Code from the repo root. Assumes fix-list items 1–3 and `rubric_version` are done or pending; builds around `_finalize` + `Review` validator as the invariant layer.

---

## Part 1 — Async batch API, in-process bounded queue, job endpoints

New `cqr/jobs.py`: `Job`, `JobError {transcript_id, error_type, message, retryable, attempts}`, `JobRunner(judge_factory, store, concurrency, max_jobs_kept=100)` with `asyncio.Queue` + N worker coroutines (`asyncio.to_thread(judge.judge, t)`), store access on loop thread only, flush every 5 + at job end, in-memory job table with eviction, optional idempotency key, circuit breaker after `CQR_CIRCUIT_THRESHOLD` (5) consecutive retryable failures → remaining items `CircuitOpen`.

Job status: `queued` → `running` → `completed` (may include failures) | `failed` (config-level abort).

`schema.py`: `Review.job_id`, `BatchAccepted`, `transcripts` max_length from `CQR_MAX_BATCH` (200); keep `list[Transcript]` (whole-body 422 on malformed).

`api.py`: `POST /review/batch` → **202**; `?wait=true` blocks up to `CQR_BATCH_WAIT_S` (60) → **200** `Job`; `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/reviews` (riskiest first); `GET /reviews?job_id=`; `Idempotency-Key` header; lifespan starts/stops workers.

`cli.py`: `--concurrency N` (`ThreadPoolExecutor`), exit **1** on any failure, exit **2** on missing file, skip malformed JSONL lines with count.

Tests: job lifecycle, idempotency, eviction, circuit, concurrency bound (8 items / 4 workers / 0.1 s sleep < 0.5 s), API shapes, 422 oversize, CLI exit codes.

---

## Part 2 — LiteLLM judge with classified errors

`requirements.txt`: `litellm>=1.50`; drop `anthropic` if unused. Import `litellm` locally in the judge.

New `cqr/errors.py`: `JudgeError{retryable, http_status, scope, attempts}` →
- `JudgeUnavailable` (503, retryable, transcript)
- `JudgeRejected` (502, terminal, config)
- `TranscriptRejected` (422, terminal, transcript)
- `JudgeOutputInvalid` (500, terminal, transcript)

`LLMJudge(model)` replaces `AnthropicJudge`: `litellm.completion(..., temperature=0, max_tokens=2000, timeout=CQR_LLM_TIMEOUT_S, num_retries=CQR_LLM_RETRIES, api_base=CQR_LLM_BASE_URL)`; **no `fallbacks`** (attribution); separate `max_output_retries` for bad JSON, previous output included in retry prompt; `_classify(exc)` maps LiteLLM exceptions → the four types; `Review.judge = "llm:<model>"`; `get_judge` accepts `llm|heuristic`, `anthropic` alias.

`api.py`: `JudgeError` exception handler → `{error_type, message, retryable, attempts}`, `Retry-After: 30` on 503; `GET /health` (no provider call).

`jobs.py`: per-item errors from `JudgeError` fields; `scope=="config"` aborts the job as `failed`; circuit counts only retryable failures.

`cli.py`: `JudgeRejected` → exit **3** after one attempt.

Tests: monkeypatch `litellm.completion`; each exception → class/status; output retry attempts; no `fallbacks` kwarg; `api_base` passed; job abort on config error (stub called once); `/health` shape.

---

## Docs

**README**: accept-then-poll flow with `curl`; Error contract subsection; Environment table (`CQR_JUDGE`, `CQR_MODEL`, `CQR_LLM_BASE_URL`, `CQR_LLM_TIMEOUT_S`, `CQR_LLM_RETRIES`, `CQR_CONCURRENCY`, `CQR_MAX_BATCH`, `CQR_BATCH_WAIT_S`, `CQR_CIRCUIT_THRESHOLD`, `CQR_STORE`); layout + test count.

**TRADEOFFS**: two architecture paragraphs (queue/concurrency knob; LiteLLM judge, fallback off, gateway via base URL); corners cut (in-memory jobs, in-process workers, whole-body 422, no auth); hardening (Postgres/Redis queue, out-of-process workers, gateway policy, re-enqueue retryables).

---

## Acceptance

- `pytest` green + 100% coverage;
- heuristic CLI run exit 0;
- API 202 → poll → reviews;
- `/health`;
- `?wait=true` 200;
- LLM path returns `judge: llm:*` with a key set (don't commit output);
- diff limited to listed files.

Two commits: `feat(api): async batch jobs with bounded in-process queue`, `feat(judge): LiteLLM-backed judge with classified errors`.

_(Full verbatim prompt is in the chat transcript of 2026-09-28.)_
