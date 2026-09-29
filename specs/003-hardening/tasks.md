# 003 — tasks

Work top to bottom. Tick each box in the same commit as the change. Details live in `PROMPT.md`.

## A. CI workflow + badge — commit `ci: add GitHub Actions test/coverage gate`  → `<pending>`
- [x] `.github/workflows/ci.yml` on push to `main`, PR, and `workflow_dispatch`; ubuntu-latest; Python 3.11
- [x] `pip install -r requirements-dev.txt`, then `pytest -q` and `coverage run --source=cqr -m pytest && coverage report --fail-under=100`
- [x] README badge at the top of the file, pointing at the workflow on `main`

## B. LICENSE + third-party notices — commit `docs: add license and ABCD attribution`  → `<pending>`
- [x] `LICENSE` — MIT, copyright "Vikram Dham" 2026
- [x] `THIRD_PARTY_NOTICES.md` — one entry for ABCD (`asappresearch/abcd`) listing the files we redistribute and reproducing the upstream license text (MIT, verified via GitHub Licenses API)
- [x] README §Setup: one line pointing at `THIRD_PARTY_NOTICES.md`

## C. README shape — commit `docs(readme): expected output, reading order, deliberate scope`  → `<pending>`
- [x] **Quick start**: an "Expected output" block that is the trimmed real output of `scripts/demo.sh` (~25 lines)
- [x] **Reading order** table near the top (`If you want to… → read…`) covering TRADEOFFS, STATUS, specs/, docs/decisions, CQR_FIXES, cqr/schema.py, cqr/rubric.py
- [x] **Deliberate scope** section above §Contract — two short paragraphs, phrased from TRADEOFFS.md
- [x] **Design principles** — a numbered list of five items

## D. Timed demo script — commit `docs: add specs/DEMO.md`  → `<pending>`
- [x] `specs/DEMO.md` — five sections with timestamps summing to ~4:30; each section has *click / say / notice*
- [x] README Reading order links to `specs/DEMO.md`

## E. Prompt caching + intent-sorted batches — commit `feat(judge): cacheable prompt prefix; batches ordered by intent`  → `<pending>`
- [x] New `build_messages(...)` returns LiteLLM messages with `SYSTEM + RUBRIC` and the reference block marked `cache_control: {"type": "ephemeral"}` on Anthropic; plain-string form on other providers
- [x] Model-prefix guard (`_supports_prompt_caching`) so non-Anthropic providers never see `cache_control`
- [x] `JobRunner.submit()` and `cli review` sort by `(intent or "", id)` before enqueue/run
- [x] `Usage` schema; `Review.usage` (input/output tokens, cache_read/creation, cost_usd); `Job.usage` is the sum
- [x] Dashboard meta line renders `cost $X` when set; `cqr show --json` includes `usage` via `.model_dump(mode="json")`
- [x] Tests: `build_messages` structure on Anthropic vs non-Anthropic, reference-before-transcript ordering, intent-ordered dispatch through JobRunner, per-review usage summed into per-job usage
- [x] TRADEOFFS §Cost — two sentences on what is cached and why intent grouping matters

## F. Structured outputs — commit `feat(judge): schema-constrained model output`  → `<pending>`
- [x] `JudgeOutput` Pydantic model = model-facing subset of `Review` (risk_flags, four signal results, sentiment_trajectory, summary) — no provenance/derived fields
- [x] `LLMJudge.judge()` passes `response_format={"type": "json_schema", ...}` with `JudgeOutput.model_json_schema()`, `strict: True`
- [x] On `UnsupportedParamsError`, fall back to text-only path once (`structured_output=False`) with the full prose schema
- [x] `_extract_json` + validation + `max_output_retries` unchanged, still active for the fallback
- [x] Rubric: `_rubric_body(structured=True)` strips the "# OUTPUT JSON SCHEMA" prose and replaces with one line; prose kept for fallback
- [x] `RUBRIC_VERSION` bumped to 1.3
- [x] Tests: response_format shape + all level enums in the schema; provenance fields NOT in JudgeOutput schema; UnsupportedParamsError triggers fallback; structured path prompt drops the prose block

## G. Content-addressed identity + `schema_version` — commit `feat(schema): transcript digest, schema version, content-based idempotency`  → `<pending>`
- [x] `SCHEMA_VERSION = "1.0"` in `cqr/schema.py`; `Review.schema_version: str` (default "" for backward-compat with pre-versioning stored reviews)
- [x] `Transcript.digest()` — sha256 over canonical JSON of `{id, source, intent, reference, turns}` (metadata excluded, sorted keys, no whitespace)
- [x] `Review.transcript_digest`, `Review.reference_version` (12-hex or `"none"`), `Review.cache_hit` (default False) — all set in `_finalize`
- [x] `Store.find(...)` + in-memory `_by_content` index; rebuilt on load; pre-versioning reviews with empty digest are not indexed (never cache-hit)
- [x] `POST /review` short-circuits on cache hit and returns `cache_hit=True`; `JobRunner._process_one` skips the judge on hit; `cqr review` prints `c` on cache-hit rows and reports `X scored, Y cached, Z failed`
- [x] `?force=true` on `POST /review`, `--force` on `cqr review`
- [x] Tests: digest determinism + stability under key reordering + independence from `metadata`; same content twice → cache_hit=True + judge NOT called; changing one character bypasses; `force=true` bypasses; find() scoped by rubric/reference/judge; pre-versioning reviews not indexed; JobRunner cache-hits skip judge; CLI `--force` cycles scored/cached correctly

## H. Stale-review detection — commit `feat: stale reviews and rereview`  → `<pending>`
- [ ] `Review.stale: bool = False` (persisted default False; computed True at read time)
- [ ] Stale rule: `review.schema_version != SCHEMA_VERSION` OR `review.rubric_version != RUBRIC_VERSION` OR (intent resolvable AND resolved reference hash != review.reference_version)
- [ ] `GET /reviews?stale=true` filter; `stale_count` field in `GET /health` response
- [ ] `cqr show` shows a stale count in its header; `cqr show --json` includes `stale` on each review
- [ ] `cqr rereview [--stale] [--all] [--judge …]` — re-runs the judge (bypassing content cache), replaces reviews; exit codes match `review`
- [ ] Tests: monkeypatch `RUBRIC_VERSION` → all reviews stale; temp `guidelines.json` change → only that intent's reviews stale; `rereview --stale` (heuristic) clears the flag
- [ ] TRADEOFFS §Versioning: replace the "needs a version stamped" line with what now exists (three versions per review, stale detection, rereview)

## Invariant test + close-out — commit `test: name the model-cannot-override invariant; docs: close 003`  → `<pending>`
- [ ] `tests/test_invariants.py` — feed `_finalize` model outputs that set `needs_human_review`, `sentiment_delta`, and `correctness` (no-reference case) to wrong values; assert computed Review ignores all three; assert digest/version fields do not depend on model output
- [ ] README §Invariants: one sentence pointing at `test_invariants.py`
- [ ] Fresh-clone acceptance: run `scripts/demo.sh`, `git status` clean afterwards
- [ ] `STATUS.md`: 003 done, demo-readiness re-checked
- [ ] `CHANGELOG.md`: 0.3 entry dated today listing A–H

## Acceptance
- [ ] `pytest` green, `coverage report --fail-under=100` passes
- [ ] `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` succeeds
- [ ] README shows the badge, Reading order, Deliberate scope, Design principles, Expected output
- [ ] `LICENSE` + `THIRD_PARTY_NOTICES.md` exist; ABCD license text is upstream's (or the flagged placeholder)
- [ ] Two `POST /review` with the same body → second `cache_hit: true`, stub called once; `?force=true` → called again
- [ ] `Review.model_fields` includes schema_version, rubric_version, reference_version, transcript_digest, usage, cache_hit, stale
- [ ] Monkeypatch `RUBRIC_VERSION` → `/reviews?stale=true` returns every review; `cqr rereview --stale --judge heuristic` → none
- [ ] `git log --oneline` shows spec commit first, one commit per A–H, then close-out
- [ ] `git status` clean after final `scripts/demo.sh`
