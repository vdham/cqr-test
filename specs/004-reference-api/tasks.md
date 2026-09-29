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
- [ ] `Transcript.reference_id: Optional[str] = None`
- [ ] `resolve_reference(t) -> tuple[str | None, str, str]` — `(text, resolution, version)`; resolution ∈ `{inline, id, intent, none}`; unknown `reference_id` raises `TranscriptRejected`
- [ ] `LLMJudge` and `HeuristicJudge` both resolve at the top of `judge()`, use the resolved text for the prompt
- [ ] `_finalize` calls `resolve_reference` to stamp `reference_id`, `reference_resolution`, `reference_version`; correctness override uses the resolved text (not just `t.reference`)
- [ ] `Review.reference_id: Optional[str] = None`; `Review.reference_resolution: str = "inline"` (default matches pre-004 behaviour where only inline references existed)
- [ ] OpenAPI examples: `with_reference` becomes a `reference_id` example; keep one inline-text example
- [ ] Tests: inline path; `reference_id` path; `intent` path; `none` path → correctness forced unverifiable; unknown `reference_id` → 422 `TranscriptRejected` with `error_type: "TranscriptRejected"`; unknown `intent` → resolution "none" (not error); `Review.reference_version` matches `GET /guidelines/{id}.version`

## D. Stale check via index + dashboard + docs — commit `feat: stale via reference index; dashboard shows reference id/version`  → `<pending>`
- [ ] `is_stale` looks up `review.reference_id` in `get_index()` when `reference_resolution in ("id", "intent")`; inline never goes stale via that path
- [ ] Dashboard: reference panel shows `reference_id · version` linking to `/guidelines/{id}`; stale badge when the review is stale from the reference path
- [ ] README §Contract gets a "References" subsection: three ways to supply, resolution order, what `reference_resolution` means; endpoint list updated
- [ ] TRADEOFFS §Reference retrieval rewritten — interface exists; production swaps the lookup behind it for a versioned KB table + retrieval when intent is unknown
- [ ] Tests: mutating a guideline text via a temp `guidelines.json` marks only that intent's `id/intent`-resolved reviews stale; inline reference reviews are never stale from the reference path; dashboard renders link + badge (smoke test)

## Close-out — commit `docs: close 004`
- [ ] `STATUS.md`: 004 complete, demo-readiness re-checked
- [ ] `CHANGELOG.md`: new session entry listing A–D
- [ ] Fresh-clone acceptance: `scripts/demo.sh` runs to completion; `git status` clean after

## Acceptance
- [ ] `pytest` green; `coverage report --fail-under=100`
- [ ] `curl localhost:8000/guidelines | jq length` → 55; `.../guidelines/shipping_issue/missing | jq .version` → 12 hex; `.../guidelines/nope/nope` → 404 ErrorBody
- [ ] `POST /review` with `{"reference_id": "shipping_issue/missing", ...}` and no `reference` → `reference_resolution: "id"`, `reference_version` matches step above, `correctness.rationale` doesn't say "no reference"
- [ ] `POST /review` with intent only → `"intent"`; with neither → `"none"` + `correctness.level == "unverifiable"`
- [ ] Error-contract status set unchanged (`200,202,404,413,422,429,500,502,503`); middleware test passes with the two new routes
