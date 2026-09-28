# Conversation Quality Reviewer — tradeoffs

## Why these signals

Five signals in two tiers, plus one context field. They were chosen to be low-correlation (a conversation can score high on one and low on another), judgeable from the transcript alone (except correctness, which needs a reference on purpose), and explainable: every level carries a rationale and turn citations, because the first question anyone asks about a score is "why."

**Tier 1 — must know.** These answer "is anything on fire" and "did it work."

- **Risk flags** — a gate, not a score. Unauthorized promises, PII mishandling, skipped verification, churn/legal/social threats, hostile agent. One flag surfaces the conversation for a human today regardless of how well it scored elsewhere; that is why it is never averaged into a composite. `needs_human_review` is the *urgent exception queue* — an `unresolved` conversation with high effort, low quality, no flag, and supported correctness deliberately does NOT route to it. That is a QA sampling job, not an urgent review, and mixing the two kills precision of the queue supervisors actually watch.
- **Resolution** — resolved / partially / deferred-with-owner / unresolved. "Deferred with a case number and a date" is a good outcome; "customer gave up" is not. Judged by the customer's goal, not the agent's claim.
- **Correctness** — supported / contradicted / unverifiable, against a reference (ABCD's own agent guidelines here). This catches the failure nothing else does: the customer accepts a wrong answer and leaves happy. Sentiment up, effort low, "resolved," and still poor quality. Politeness is irrelevant to this signal.

**Tier 2 — explain the experience.** Coaching and QA use these.

- **Customer effort** — repeats, transfers, re-explaining, being pushed to self-serve. The best leading indicator of churn and the one CSAT cannot see. Resolved-but-exhausting is a real and common failure mode.
- **Interaction quality** — acknowledged impact before procedure, used the customer's specifics, took ownership, clear next step. The one agent-behavior signal, defined by observable behaviors so it is not vibes.

**Context — sentiment trajectory.** Scored per customer turn and shown as a sparkline, not a quality dimension. Average sentiment punishes agents for customers who arrive angry; the arc (angry → calm) is the useful part, and it correlates heavily with the two tiers above, so it explains rather than scores.

Left out on purpose: *escalation likelihood* (derivable from unresolved + negative delta + flags), *CSAT prediction* (a guess about a survey; score the observable inputs instead), *handle time* (already on every dashboard, rewards the wrong behavior).

## Data

ABCD (ASAPP's dataset) plus nine synthetic transcripts. ABCD is the right base — it is the domain, and it ships the guidelines the agents were supposed to follow, which is what makes correctness checkable rather than "unverifiable" everywhere. Its weakness is that crowdworkers are polite: risk flags, escalation, and wrong answers almost never occur. The synthetic set exists to make every signal fire, and each one is labeled with what it is meant to exercise, so it doubles as a seed eval set. Two of them (`syn-01`, `syn-09`) are the same policy rule (missing item: reship at 7+ days, wait under 7) violated in opposite directions.

## Architecture

One LLM call per conversation with an anchored rubric, returning JSON validated against a Pydantic schema; the schema is the contract. A regex baseline judge runs with no API key so the pipeline works offline, and it shows in the demo what a naive approach gets wrong: it cannot do correctness at all, and `syn-01` and `syn-09` sail through it unflagged. Two derived fields are computed in code, never trusted from the model: `needs_human_review` (any medium+ flag or contradicted correctness) and `sentiment_delta`. Correctness is forced to `unverifiable` when no reference exists, whatever the model says.

Consumable three ways: `POST /review` (one transcript → review), `POST /review/batch` (accept-then-poll), `GET /reviews` sorted riskiest-first with a `needs_human_review` filter, and a single-page dashboard on top. A CLI does batch runs.

**Batch queue: bounded, in-process, single-purpose.** `POST /review/batch` enqueues a `Job` onto `asyncio.Queue` and returns 202 immediately; `CQR_CONCURRENCY` worker coroutines pull items and run the (blocking) judge via `asyncio.to_thread`, so the loop stays free to serve `/jobs` polling. Store mutations happen only on the loop thread — no locking, no cross-thread contention. A latched per-job circuit breaker (`CQR_CIRCUIT_THRESHOLD` consecutive retryable failures) short-circuits the remainder of a doomed job; a `JudgeRejected` (bad key etc.) flips the whole job to `failed` since every remaining item would repeat the same failure. Backpressure is enforced upstream with `CQR_MAX_BATCH` (whole-body 422 on oversize) rather than an unbounded server-side queue.

**Judge: LiteLLM with `fallbacks=` deliberately off.** The judge speaks whatever provider LiteLLM does — Anthropic, OpenAI, Azure, or an OpenAI-compatible gateway pointed at by `CQR_LLM_BASE_URL`. Attribution is more important than availability for a scoring product: fallbacks between models would introduce silent quality drift across `judge` values, which corrupts every downstream trend. Errors are classified via `_classify_litellm_error` into the four `JudgeError` subclasses — retryable/terminal × transcript/config — so callers get typed failures instead of provider-shaped exceptions.

## What I'd harden for production

- **Judge reliability.** Temperature 0 and retries on invalid JSON are not enough. Add self-consistency (2–3 samples, majority), a small calibration set with human labels per signal, and per-signal agreement tracking. Split the correctness check into its own call with retrieval over the policy corpus rather than passing the whole guideline.
- **Reference retrieval.** Here the reference is chosen by ABCD's known intent. In production the intent is not known; you need intent classification plus retrieval over the KB, and "unverifiable" becomes a metric to drive down.
- **Cost and latency.** ~1 call per conversation is fine for QA sampling; for 100% coverage, run risk flags on a cheap model or classifier first and only send flagged/sampled conversations to the full rubric.
- **Voice.** Nothing here handles ASR errors, overlapping speech, or silence, all of which affect effort and sentiment.
- **Versioning.** The rubric is the product. It needs a version stamped on every review and a regression suite so a prompt edit does not silently shift every score.
- **Storage, auth, PII.** JSON file → a table; no auth; transcripts contain PII and are stored raw. Redact before the judge sees them.
- **Queue: broker + out-of-process workers.** Move jobs to Postgres (or Redis) so a restart doesn't drop work; run workers as separate processes so a stuck provider call doesn't hurt the API loop. Re-enqueue retryable failures with exponential backoff and a per-job attempt cap, instead of the current single-shot circuit-breaker fail.
- **Judge: gateway policy at the gateway.** Rate limiting, cost tracking, response caching, redaction of PII from prompts belong in the LiteLLM proxy (`CQR_LLM_BASE_URL`), not sprinkled through the judge. Keep the judge single-purpose (score one transcript, return one Review); let the gateway own multi-tenant policy.
- **Layered risk detection.** Union deterministic detectors (regex PII, keyword promise/threat, action-slug adherence from `delexed[].targets`) with the LLM's risk flags rather than trusting either alone. The gate needs recall over parsimony; a false positive on the human-review queue is far cheaper than a missed PII leak.
- **Untrusted content at two boundaries.** Prompt-injection resistance at the judge (transcripts arrive as untrusted strings; ignore any instructions inside them) and output encoding at the UI. Redact with **typed placeholders** (`<CARD_NUMBER>`, `<EMAIL>`, `<NAME_1>`) rather than deletion so `pii_mishandling` detection still fires and "used the customer's specifics" still parses; keep the reversal table separate.
- **Procedure adherence via ABCD's own labels.** `kb.json` currently gates only subflow-slug validation. It also carries the canonical action sequence per subflow, and ABCD's `delexed[].targets` records the action each agent turn was taking. Restricted to unconditional verification steps (pull-up-account, validate-purchase), diffing the observed against the expected sequence gives a deterministic `missing_disclosure` detector that never guesses — a natural companion to the LLM correctness signal, not a replacement.

## What I'd measure to know it's working

- **Agreement with human QA** per signal (Cohen's κ), tracked over time, with a target set per tier: risk flags need high recall; Tier 2 can tolerate lower κ.
- **Risk flag precision** in the human-review queue: what fraction of surfaced conversations did a supervisor act on? If it is low, the gate is noise and people stop looking.
- **Correctness coverage**: share of conversations that are `unverifiable`. Drives KB and retrieval work.
- **Downstream**: do conversations scored high-effort churn more? Do contradicted conversations generate repeat contacts? If the signals don't predict anything the business cares about, they are the wrong signals.
- **Drift**: score distributions per agent/team per week; a sudden shift means either the world changed or the judge did.

## Where I cut corners on purpose

- Single judge model, single sample, no ensembling.
- Regex baseline is deliberately crude; it is a foil, not a fallback.
- The heuristic patterns were written after the synthetic transcripts; its eval score shows the pipeline works, not that regex is a calibrated baseline.
- Reference lookup is by ABCD intent key with a fuzzy match; no retrieval.
- Unit and API paths are covered by hermetic tests (100% line coverage); model-behaviour evaluation is limited to nine hand-labeled synthetic conversations. No auth; JSON file store; no redaction.
- The job table is in-memory (LRU-capped at `CQR_MAX_JOBS_KEPT`); workers run in-process. A restart drops queued and running jobs. This is deliberately small — the point is to prove out the shape of the async contract without pulling in a broker.
- `POST /review/batch` uses a whole-body 422 on any invalid transcript rather than per-item validation. Clients get a fast, definitive answer; the alternative (partial acceptance) makes it too easy to ship half-broken batches downstream.
- The UI is one HTML file with no build step. It exists to make the rationale and turn citations visible in a live demo, not to be a product.
- Nine synthetic transcripts, hand-written. Enough to show every signal firing; nowhere near enough to measure anything.
