# ADR 0003 — No cross-model fallback in the judge

**Status.** Accepted (applies to spec 002).

**Context.** LLM gateways and LiteLLM offer automatic fallback to another model on rate limit or outage. For a chat product that is pure upside.

**Decision.** The judge uses exactly one model per run, recorded in `Review.judge` alongside `rubric_version`. Provider/region fallback for the *same* model is acceptable; cross-model fallback is off. Retryable failures are surfaced (or re-enqueued) rather than silently rerouted.

**Consequences.** A batch may finish with failures during an outage instead of silently mixing judges. Every score remains attributable to one (model, rubric) pair, which is what agreement tracking against human labels requires.
