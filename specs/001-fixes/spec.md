# 001 — Review fixes

**Why.** A code review against the ASAPP brief (2026-09-27, commit e63b71e) found the architecture sound but surfaced two real bugs, several docs-vs-code contradictions, and repo hygiene issues that undercut the "schema is the contract" story. Full findings and rationale: see `CQR_FIXES.md` (the instruction file) — this spec is the short form.

**Scope.** Contract enforcement (typed levels, evidence validation, input validation, `rubric_version`), guideline lookup correctness, rubric wording, eval/dashboard hygiene, and documentation alignment. No new signals, no new infrastructure.

**Out of scope.** Async batch, queue, provider error handling, LiteLLM — see `specs/002-batch-litellm`.

**Done when.** All boxes in `tasks.md` ticked and the acceptance checklist at the bottom of `CQR_FIXES.md` passes.
