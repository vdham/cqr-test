# ADR 0001 — No composite quality score

**Status.** Accepted (initial plan had `overall: 0.72`; removed before implementation).

**Context.** A single number is what dashboards want, and the first plan produced one by weighting five signals.

**Decision.** Expose five signals in two tiers plus a context field; never average them. Risk flags are a gate (`needs_human_review`), not a score.

**Consequences.** A severe policy violation or PII exposure cannot be offset by politeness or low effort. Consumers who need a single number must define their own weighting explicitly, downstream, where the choice is visible.
