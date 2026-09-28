# 001 — tasks

Tick in the same commit as the change. Details for every item are in `CQR_FIXES.md` (same section letters).

## A. Contract enforcement — commit `fix(schema): …`  → `b7c8d5e`
- [x] A1 Type-enforce signal levels (typed result models; normalize case in `_finalize`; `"CONTRADICTED"` → `needs_human_review=True`; `"very high"` → `ValidationError`)
- [x] A2 Validate evidence turn citations; add `Review.warnings`
- [x] A3 Add `RUBRIC_VERSION` and `Review.rubric_version`; show in dashboard meta
- [x] A4 `Transcript` validation: non-empty turns, unique contiguous `idx`
- [x] Tests for A1–A4; coverage 100%

## B. Guideline lookup — commit `fix(loader): …`  → `11c3eee`
- [x] B1 Exact title match → aliases → token overlap; drop `status`/`manage` from stop words; add `refund_status`, `refund_update` aliases
- [x] B1 Validate subflow slug against `data/abcd/kb.json` keys; warn on unknown
- [x] B1 Parametrized test over all 55 `(flow, subflow)` pairs; `refund_status` → `Refund Status`
- [x] B2 TRADEOFFS sentence on the future `kb.json` procedure check

## C. Rubric — commit `fix(rubric): …`  → `022753f`
- [x] C1 §3 wording: judge customer-facing claims + observable actions; don't penalize unseen tool steps
- [x] C1 Remove "universally known" clause from `build_user_prompt`; bump `RUBRIC_VERSION` to 1.1

## D. Eval, dashboard, hygiene — commit `fix(misc): …`  → `2785817`
- [x] D1 Eval parser accepts `a|b`; `syn-02` label updated; `--store`, header prints judge + rubric version
- [x] D2 Dashboard: `esc()` on id, summary, rationale, intent, judge
- [x] D3 TRADEOFFS: heuristic regexes are fixture-informed
- [x] D4 CLI `show --needs-review --source --json`; shared sort key; `--fresh`; `--limit` default aligned
- [x] D5 README: callable-function snippet
- [x] D6 OpenAPI: derived fields not optional (documented as "always present"; kept runtime defaults so direct construction still works)

## E. Docs — commit `docs: …`  → `daee7b3`
- [x] E1 Fix TRADEOFFS "no tests" line; README eval-script and "first 15 ABCD" lines; document `/review` idempotency and batch limitation
- [x] E2 Delete `docs/jev-analysis.md`; rename `docs/PLAN.md` → `docs/ORIGINAL_PLAN.md` with "superseded — what changed" header
- [x] E3 Committed `examples/reviews.anthropic.json` (22/22 on synthetic); eval table in README
- [x] E4 TRADEOFFS additions: layered risk detection; untrusted content + typed placeholders; `needs_human_review` semantics; unused ABCD annotations

## Acceptance
- [x] `CQR_FIXES.md` acceptance checklist items 1–8 pass
- [x] `STATUS.md` updated
