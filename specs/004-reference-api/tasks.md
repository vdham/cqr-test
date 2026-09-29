# 004 — tasks

Work top to bottom. Tick each box in the same commit as the change.

## A. Reference model + index API — commit `feat(loader): Reference model; GuidelineIndex.get/list`  → `<pending>`
- [x] `Reference` Pydantic model in `cqr/schema.py` — `reference_id, flow, subflow, version, text, policy_lines, procedure_lines, source`
- [x] `GuidelineIndex.list() -> list[Reference]` returning all 55 (kb.json is authoritative — iterates canonical slugs)
- [x] `GuidelineIndex.get(reference_id) -> Reference | None`; strict on flow_key + subflow slug identity (rejects cross-flow / wrong-subflow fuzzy matches)
- [x] `reference_for(flow, subflow)` kept fuzzy for backward compat (used by `_abcd_convo_to_transcript` + pre-004 tests); `get` is the strict-id path
- [x] `_FLOW_INVERSE`, `_ALIAS_INVERSE`, `_subflow_title_to_slug` helpers
- [x] `cqr/loader.py::get_index()` — module-level singleton; reads `$CQR_GUIDELINES` (default `data/abcd/guidelines.json`); returns None if the file is absent
- [x] Tests (26): `Reference` shape; `_subflow_title_to_slug` alias/snake_case cases; `list()` returns 55; every kb slug's reference_id is in the list; `list()` sorted; every reference has version + text + source; policy/procedure lines populated; `get` known/unknown/malformed ids; bogus flow → None; bogus slug → None; cross-flow slug → None; defensive miss-when-subflow-empty; version matches hash of text; kb-less fallback list; `get_index` singleton + reset + no-kb

## B. Endpoints — commit `feat(api): GET /guidelines, GET /guidelines/{reference_id}`  → `<pending>`
- [x] `GET /guidelines` → `list[GuidelineSummary]` = 55 rows of `{reference_id, flow, subflow, version}` (no `text`)
- [x] `GET /guidelines/{flow_key}/{subflow_key}` → full `Reference` | 404 `ErrorBody`; missing index → 404 (rather than crash)
- [x] `ERROR_RESPONSES` gets `list_guidelines: {}` and `get_guideline: {404}`; `EXPECTED_MATRIX` in `test_error_contract` updated (both routes in the drift check)
- [x] `GET /health` gains `guidelines: {count, source, index_version}` where `index_version = reference_version(concat of all Reference.version)` — 12-hex
- [x] Tests: list returns 55 rows with the four documented fields; empty when index absent; get returns full Reference with text; unknown flow / unknown slug / missing index all 404 `ErrorBody`; health guidelines block populated + zeroed correctly

## C. Server-side resolution — commit `feat(judge): resolve reference by id or intent; record how`  → `<pending>`
- [x] `Transcript.reference_id: Optional[str] = None`
- [x] `resolve_reference(t)` in `cqr/loader.py` — inline > id > intent > none; unknown `reference_id` raises `TranscriptRejected`; unknown `intent` falls to `none`
- [x] `LLMJudge.judge` resolves at the top and passes text into `build_messages` (so the prompt reflects the resolved reference, not `t.reference`)
- [x] `_finalize` calls `resolve_reference` again to stamp `reference_id`, `reference_resolution`, `reference_version`; correctness-forced-unverifiable now keys on the resolved text (not `t.reference`)
- [x] `Review.reference_id`, `Review.reference_resolution` (default "inline" — matches pre-004 stored reviews where only inline was possible)
- [x] OpenAPI examples restructured: `minimal` (no reference), `by_reference_id` (preferred), `inline_reference` (fallback)
- [x] Tests: each resolution path (`inline`, `id`, `intent`, `none`); unknown `reference_id` → 422 `TranscriptRejected`; unknown `intent` → "none" + unverifiable; `Review.reference_version` matches `GET /guidelines/{id}.version`; id path with no index raises

## D. Stale check via index + dashboard + docs — commit `feat: stale via reference index; dashboard shows reference id/version`  → `<pending>`
- [x] `is_stale` reads `review.reference_id` and compares to `get_index().get(...).version` when `reference_resolution in ("id","intent")`; inline stays never-stale; no reference_id → no reference check
- [x] Dashboard reference panel: resolution + linked `reference_id` + version; stale badge when the review is stale
- [x] README §Contract gains a References subsection listing the four resolution paths; endpoint list updated to include `/guidelines*`
- [x] TRADEOFFS §Reference retrieval rewritten
- [x] Tests: mutating a guideline while the review is `id`- or `intent`-resolved marks it stale; inline stays never-stale even when guidelines are wiped; a `resolution="none"` review has no reference to check

## Close-out — commit `docs: close 004`
- [x] `STATUS.md`: 004 complete, demo-readiness re-checked
- [x] `CHANGELOG.md`: `0.6` entry listing A–D
- [x] Fresh-clone acceptance: `scripts/demo.sh` runs to completion; `git status` clean after

## Acceptance
- [x] `pytest` green (426 tests); `coverage report --fail-under=100` passes
- [x] `curl localhost:8000/guidelines | jq length` → 55; `.../guidelines/shipping_issue/missing | jq .version` → 12 hex; `.../guidelines/nope/nope` → 404 ErrorBody
- [x] `POST /review` with `{"reference_id": "shipping_issue/missing"}` and no `reference` → `reference_resolution: "id"`, `reference_version` matches, `correctness` scored against the resolved text
- [x] `POST /review` with intent only → `"intent"`; with neither → `"none"` + `correctness.level == "unverifiable"`
- [x] Error-contract status set unchanged (`200,202,404,413,422,429,500,502,503`); middleware test passes with the two new routes
