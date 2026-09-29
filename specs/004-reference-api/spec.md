# 004 — Reference (guideline) API + server-side resolution

**Why.** `correctness` is the signal that separates "happy customer, wrong answer" from actually-good work — and it only fires when a reference policy is attached. Today's API asks callers to paste that policy text into every request; `Transcript.intent` is documented as *"used to select reference guidelines"* but the API never honours it (only the offline loader does, and only for ABCD data); and there's no way to see what a review was actually judged against beyond the raw prompt. Three concrete gaps:

1. **Discoverability.** A developer building against this SDK has no way to enumerate the 55 canonical references — they'd have to open `data/abcd/guidelines.json` and figure out the schema.
2. **Repetition + drift.** Every conversation on the same intent ships the same multi-hundred-token reference block. If the caller ever paraphrases it, cache-hits (spec 003 §E) break and stale detection (spec 003 §H) misfires.
3. **Provenance.** `Review.reference_version` is 12 hex — you can tell a review was scored against *some* reference, but not which one, and you can't re-fetch the text to argue with the verdict.

This spec closes all three: the reference set becomes a first-class HTTP resource with stable ids, the transcript can point at it by id (or by intent) instead of embedding text, and every review records how the reference was resolved.

**Scope.** Items A–D below, one commit each. Fully additive at the schema level; every existing client keeps working (inline `reference` still wins).

- **A**. `Reference` Pydantic model + `GuidelineIndex.list()` / `.get(reference_id)` + a module-level `get_index()` singleton.
- **B**. `GET /guidelines` (55-row index, no text) and `GET /guidelines/{flow_key}/{subflow_key}` (full `Reference` or 404); `/health` gains a `guidelines` block; error-contract matrix updated.
- **C**. `Transcript.reference_id: str | None`; `resolve_reference(t) -> (text, resolution, version)` used by both the judge (for the prompt) and `_finalize` (for provenance stamping); resolution order is `inline > id > intent > none`; unknown `reference_id` → 422 `TranscriptRejected`; unknown intent → resolution "none" (advisory, not an error). New `Review.reference_id` + `Review.reference_resolution` record what happened.
- **D**. Spec 003 §H's stale check uses `get_index()` (compare `review.reference_version` to `get_index().get(review.reference_id).version` when resolution is `id`/`intent`; inline stays never-stale). Dashboard's reference panel shows `reference_id · version` with a link to `/guidelines/{id}` and a "stale — guideline changed" badge when applicable. README §Contract gains a References subsection; TRADEOFFS §Reference retrieval rewritten.

**Explicit non-goals.**

- **No upload / write API for guidelines.** The reference set is read-only in this prototype. Production would need `POST /guidelines`, versioning, review/approval workflow, and multi-tenant isolation — an order of magnitude more than this repo's scope.
- **No retrieval / search over guideline text.** Every reference lookup is by id today; the interface is designed so a KB-retrieval backend can slot in behind `resolve_reference` later without changing the contract.
- **No intent classification.** `intent` is still supplied by the caller; unknown intents resolve to `none` rather than being classified from the transcript.
- **No database.** `get_index()` reads `data/abcd/guidelines.json` at startup; the store stays JSON-file backed.

**Constraints for the whole spec.** Additive schema changes only (`Transcript.reference_id`, `Review.reference_id`, `Review.reference_resolution` all optional; existing pre-004 stored reviews still load). Tests hermetic, coverage 100% on `cqr/*`. The five signals, tiers, one-call design, and content-address contract (spec 003) unchanged.

**Done when.** All boxes in `tasks.md` ticked, the acceptance checklist at the bottom of `PROMPT.md` passes, `STATUS.md` updated.
