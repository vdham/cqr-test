# ADR 0002 — One LLM call per conversation

**Status.** Accepted (initial plan had one call per signal, run in parallel).

**Context.** Per-signal calls make each rubric independently tunable but cost 5× and lose shared context between signals (e.g. correctness and resolution both depend on the same customer goal).

**Decision.** One call with the full anchored rubric, returning one JSON object validated against `Review`. Derived fields (`needs_human_review`, `sentiment_delta`, correctness-without-reference) are computed in code, never trusted from the model.

**Consequences.** Cheaper and coherent; harder to iterate one signal in isolation. Correctness is the first signal to split out in production (own call with retrieval over the policy corpus) because it is the hardest and the most reference-dependent.
