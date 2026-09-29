# Status

_Last updated: 2026-09-28 — by: Vikram_

| Spec | Goal | State | Next task |
|---|---|---|---|
| [001-fixes](specs/001-fixes/tasks.md) | Close the review findings: contract enforcement, guideline lookup, rubric, docs | **done** (`b7c8d5e`..`daee7b3`) | — |
| [002-batch-litellm](specs/002-batch-litellm/tasks.md) | Async batch jobs with bounded queue; LiteLLM judge with classified errors; hardened error contract | **done** (`0773603`, `a94396a`, `2587866`) | — |
| [003-hardening](specs/003-hardening/tasks.md) | Eight practices — CI, licensing, README shape, timed demo, prompt caching, structured outputs, content-addressed identity, stale detection | **done** (`9e5846e`..`1d566a6`) | — |

## Done this session
- Spec `003-hardening` complete: A. GitHub Actions CI with 100% coverage gate + README badge · B. MIT `LICENSE` + `THIRD_PARTY_NOTICES.md` with ABCD's upstream license · C. README gains Reading order, Deliberate scope, Design principles, and a fresh Expected-output block · D. `specs/DEMO.md` — timed 4:30 walkthrough · E. Cacheable prompt prefix (system+rubric+reference as `cache_control: ephemeral` on Anthropic), intent-sorted batches, per-review `usage` and per-job usage sum · F. `JudgeOutput` model-facing subset + `response_format=json_schema strict` with graceful fallback on `UnsupportedParamsError` · G. `Transcript.digest()`, `SCHEMA_VERSION`, per-review `transcript_digest` / `reference_version` / `cache_hit`, `Store.find(...)` content index, `POST /review?force=true` and `cqr review --force` to bypass · H. `Review.stale` computed at read time, `GET /reviews?stale=` filter, `stale_count` on `/health`, `cqr rereview [--stale|--all]`.
- Invariant test named: `tests/test_invariants.py` asserts no model output can override the derived or versioning fields.

## Next
- No queued spec. Natural next candidates: a real (Postgres or Redis) job broker, PII redaction with typed placeholders at ingest, and a larger labeled synthetic set to make judge-accuracy claims meaningful.

## Blocked / open questions
- None.

## Demo readiness
- [x] `pytest` green, coverage 100% on `cqr/*` (see the CI badge at the top of the README)
- [x] Heuristic path runs end-to-end with no key (`python -m cqr.cli review --synthetic ... --judge heuristic`)
- [x] `scripts/demo.sh` starts the API on a scratch store copy, prints URLs for `syn-06`/`syn-01`/`syn-05`, drives a batch through `POST /review/batch?wait=true`, all with no API key; `git status` clean after the run
- [x] `POST /review/batch` returns 202 with a `job_id`; `GET /jobs/{id}` and `/jobs/{id}/reviews` return the async result
- [x] `GET /health` reports which judge/model is live and how many stored reviews are stale
- [x] `JudgeError` handler returns typed HTTP responses with `Retry-After: 30` on 503
- [x] Every non-2xx serializes to `ErrorBody` (except FastAPI's own 422), and `tests/test_error_contract.py` pins the spec against the code
- [x] Dashboard shows `syn-01` with `correctness=contradicted` on the committed LLM run (`CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app`)
- [x] `TRADEOFFS.md` has no contradictions with the code
- [x] Two `POST /review` calls with the same body → second has `cache_hit: true` and the judge is called once; `?force=true` bypasses
- [x] Bumping the rubric version marks every stored review stale; `cqr rereview --stale --judge heuristic` clears the flag
- [x] Prompt has a cacheable prefix on Anthropic (system + reference marked `cache_control: ephemeral`); non-Anthropic providers get plain strings and never see the kwarg
- [x] `LICENSE` (MIT), `THIRD_PARTY_NOTICES.md` (ABCD MIT reproduced), CI badge on the README, `specs/DEMO.md` for the walkthrough
- [x] The model-cannot-override invariant is named in `tests/test_invariants.py`
