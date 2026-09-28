# Changelog

## Unreleased
- specs/002-batch-litellm: (pending — awaits `PROMPT.md`)

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
