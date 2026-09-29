# 003 — Hardening (evidence, credibility, cost)

**Why.** Eight practices that make a demo repo cheaper to run, more credible to a reviewer, and less likely to rot: CI evidence, an actual license and third-party attribution, README shape that answers "where do I look first," a timed demo script, prompt caching so a real run doesn't burn tokens, structured outputs so the model can't emit unparseable JSON, content-addressed review identity so re-running the same transcript doesn't re-bill the same tokens, and stale-review detection so a rubric change doesn't silently invalidate the store.

Each item is independently useful and independently reversible. Taken together they turn "here's a working demo" into "here's a demo with the honesty of a small operational product."

**Scope.** Items A–H below, nothing else. One commit per item.

- **A**. `.github/workflows/ci.yml` + README badge — tests+coverage as automated evidence.
- **B**. `LICENSE` (MIT) + `THIRD_PARTY_NOTICES.md` covering the ABCD files we redistribute.
- **C**. README gains **Expected output** (from a real `demo.sh` run), **Reading order**, **Deliberate scope**, **Design principles**.
- **D**. `specs/DEMO.md` — a timed 4:30 script for the walkthrough.
- **E**. Cacheable prompt prefix + intent-sorted batches + per-review `usage` (tokens, cost, cache hits) + per-job usage sum.
- **F**. Structured outputs (`response_format` json_schema) with graceful fallback for providers that don't support it.
- **G**. `Transcript.digest()`, `SCHEMA_VERSION`, `Review.transcript_digest`/`reference_version`/`schema_version`/`cache_hit`; content-based idempotency across `POST /review`, batch, and the CLI; `?force=true` / `--force` to bypass.
- **H**. `Review.stale` computed at read time; `GET /reviews?stale=true`; `cqr rereview [--stale] [--judge …]` that re-runs the judge and replaces stored reviews.

**Explicit non-goals.** No new signals. No composite score. No auth. No database, no broker, no out-of-process workers. Rubric wording changes only where a task requires it (E + F bump `RUBRIC_VERSION`). The `Transcript → Review` contract stays backward compatible — every new field is optional with a safe default so old stored reviews still load.

**Constraints for the whole spec.** Tests stay hermetic. Coverage stays 100% of `cqr/*`. Every schema change is additive. The five signals, the two tiers, and one-call-per-conversation do not change.

**Done when.** All boxes in `tasks.md` ticked, the acceptance checklist at the bottom of `PROMPT.md` passes, `STATUS.md` and `CHANGELOG.md` updated.
