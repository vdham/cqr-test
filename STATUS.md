# Status

_Last updated: 2026-09-28 — by: Vikram_

| Spec | Goal | State | Next task |
|---|---|---|---|
| [001-fixes](specs/001-fixes/tasks.md) | Close the review findings: contract enforcement, guideline lookup, rubric, docs | **done** (`b7c8d5e`..`daee7b3`) | — |
| [002-batch-litellm](specs/002-batch-litellm/tasks.md) | Async batch jobs with bounded queue; LiteLLM judge with classified errors | not started | Part 1: `cqr/jobs.py` (`JobRunner` + bounded queue) |

## Done this session
- Added the management scaffold (`AGENTS.md`, `STATUS.md`, `specs/`, ADRs, `CHANGELOG.md`, `CQR_FIXES.md`).
- Closed all of `specs/001-fixes` in five commits (§A–§E). 229 hermetic tests, 100% line coverage on `cqr/*`, heuristic eval 18/22, Anthropic eval 22/22 (committed at `examples/reviews.anthropic.json`).

## Next
- Work `specs/002-batch-litellm/tasks.md` Part 1 (in-process bounded queue + job endpoints), then Part 2 (LiteLLM judge with classified errors). Full instructions at `specs/002-batch-litellm/PROMPT.md`.

## Blocked / open questions
- None.

## Demo readiness
- [x] `pytest` green (229 tests), coverage 100% on `cqr/*`
- [x] Heuristic path runs end-to-end with no key (`python -m cqr.cli review --synthetic ... --judge heuristic`)
- [x] Dashboard shows `syn-01` with `correctness=contradicted` on the committed Anthropic run (`CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app`)
- [x] `TRADEOFFS.md` has no contradictions with the code
