# Changelog

## Unreleased
- (nothing queued)

## 0.6 — 2026-09-29
- specs/004-reference-api complete — references become a first-class HTTP resource + server-side resolution:
  - **A** (`6426bf3`) `Reference` Pydantic model + `GuidelineIndex.list()`/`get(reference_id)` (strict-id, rejects cross-flow fuzzy matches) + `get_index()` module singleton reading `$CQR_GUIDELINES`.
  - **B** (`abaff4b`) `GET /guidelines` (55-row index, no text) + `GET /guidelines/{flow_key}/{subflow_key}` (full `Reference` | 404 `ErrorBody`); `/health` gains a `guidelines: {count, source, index_version}` block; error-contract matrix + drift-tracking middleware updated for the two new routes.
  - **C** (`c2bc53b`) `Transcript.reference_id`, `resolve_reference()` with order `inline > id > intent > none` (unknown `reference_id` → 422 `TranscriptRejected`, unknown intent → `"none"`); `Review.reference_id` + `Review.reference_resolution` on every review; OpenAPI examples restructured (minimal / by_reference_id / inline_reference).
  - **D** (`b83f858`) `is_stale` uses `get_index()` for id/intent-resolved reviews; dashboard reference panel shows resolution + `reference_id` (linked to `/guidelines/{id}`) + `version` + a stale badge when the guideline has drifted; README §Contract gets a References subsection; TRADEOFFS §Reference retrieval rewritten (the interface now exists; production swaps the lookup behind it).

## 0.5 — 2026-09-28
- specs/003-hardening complete — eight practices for a demo repo that's cheaper to run and more credible:
  - **A** (`ac72033`) GitHub Actions CI: pytest + `coverage report --fail-under=100` on push/PR/dispatch; README badge on `main`.
  - **B** (`6a30464`) `LICENSE` (MIT, Vikram Dham 2026) + `THIRD_PARTY_NOTICES.md` reproducing ABCD's upstream MIT license (verified via `gh api repos/asappresearch/abcd/license`).
  - **C** (`d191afa`) README grew Reading-order table, Expected-output block, Deliberate-scope section, and a five-item Design-principles list.
  - **D** (`144b432`) `specs/DEMO.md` — 4:30 walkthrough script with click/say/notice per section.
  - **E** (`63bcb41`) Cacheable prompt prefix: system+rubric and the reference block marked `cache_control: ephemeral` on Anthropic; batches enqueued in `(intent, id)` order so consecutive calls share the prefix; `Review.usage` + `Job.usage` record tokens and cost.
  - **F** (`47054e3`) Structured outputs: `JudgeOutput` Pydantic model = the model-facing subset of `Review` (no provenance); passed as `response_format=json_schema strict`; graceful fallback on `UnsupportedParamsError`. Rubric bumped 1.2 → 1.3.
  - **G** (`f66099f`) Content-addressed identity: `Transcript.digest()`, `SCHEMA_VERSION`, `Review.transcript_digest` / `reference_version` / `cache_hit`; `Store.find(...)` in-memory index; `POST /review?force=true`, `cqr review --force`.
  - **H** (`1d566a6`) Stale-review detection: `cqr/staleness.py::is_stale`, `Review.stale` (computed at read time), `GET /reviews?stale=`, `stale_count` in `/health`, `cqr rereview [--stale|--all]`.
  - Close-out: `tests/test_invariants.py` names the model-cannot-override invariant (nine assertions across derived + versioning fields).

## 0.4 — 2026-09-28
- Close-out pass on the error contract and demo evidence:
  - `POST /review/batch` no longer errors when lifespan hasn't started the runner — it lazy-starts on the first submit. 429 is reserved for real `QueueFull`.
  - README error table gains a **Client should check** column so 422's two-body-shapes contract is spelled out; regenerated via `scripts/render_error_table.py`.
  - Anthropic run refreshed on the current rubric (1.1) and LiteLLM judge — `examples/reviews.anthropic.json` stamps `"judge": "llm:claude-sonnet-4-5"`; eval score is **22/22**.
  - `scripts/demo.sh` — one-shot demo: start the API, print URLs for `syn-06`/`syn-01`/`syn-05`, drive `POST /review/batch?wait=true` against `tests/fixtures/batch3.json`. Works with no API key.
  - README + TRADEOFFS interviewer read-through: test count updated to 315, contract example refreshed with `judge: llm:*` + `rubric_version` + `warnings`, env table adds `CQR_MAX_QUEUE_SIZE` + `CQR_MAX_BODY_BYTES`, layout section mentions the render script and demo.sh.

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
