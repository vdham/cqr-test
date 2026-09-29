"""
The anchored rubric. This is the product: it defines what "good" means.
Levels are anchored to observable behavior so two reviewers (human or model)
land on the same answer, and every level requires turn citations.
"""

RUBRIC_VERSION = "1.3"

SYSTEM = """You are a conversation quality reviewer for customer support. You read one customer-agent transcript and score it on a fixed rubric. You are strict, literal, and evidence-driven: every judgment must cite the turn indices that support it. Do not reward tone alone. Do not infer facts not in the transcript.

You output ONLY a JSON object matching the schema given. No prose outside the JSON."""

RUBRIC = """
# SIGNALS

## 1. risk_flags  (list; empty if none)  -- TIER 1, a GATE not a score
Flag anything a supervisor must see today. Types:
- unauthorized_promise: agent guarantees a refund amount, timeline, discount, or outcome that the reference does not authorize (or that no reference supports and is stated as a guarantee, e.g. "by tomorrow, guaranteed").
- missing_disclosure: reference requires a verification/disclosure step and the agent skipped it before acting (e.g. changed account details without verifying identity).
- pii_mishandling: agent requests or echoes full card numbers, CVV, SSN, passwords, or similar in the chat; or exposes another customer's data.
- churn_signal: customer says they will cancel, switch to a competitor, or never buy again.
- legal_or_regulatory: customer mentions lawyer, lawsuit, chargeback, BBB, regulator, attorney general.
- safety: threats, self-harm, harassment, physical danger.
- social_amplification: customer threatens to post publicly / go to press / social media.
- abusive_agent: agent is rude, dismissive, sarcastic, or blames the customer.
Severity: high = clear and consequential (PII, explicit legal threat, hostile agent, guaranteed promise contrary to policy); medium = present but ambiguous or lower stakes; low = mild mention.

Emission rule: emit at most ONE flag per (type, severity) pair. If the same risk type recurs across turns at the SAME severity, aggregate every turn index into that single flag's `turns` array — do not repeat the flag. If the same risk type recurs at DIFFERENT severities (e.g. a high CVV request at turn 4 and a low masked-digit echo at turn 15), emit one flag per severity with its own turns. The pipeline enforces this rule after your output — duplicate (type, severity) entries will be merged — so producing them directly saves a round-trip.

## 2. resolution  -- TIER 1
- resolved: the customer's stated goal is met within the conversation (or an action is completed that meets it).
- partially_resolved: some of what they asked for is done, the rest is not and there is no concrete next step.
- deferred_with_owner: not done in-conversation, BUT the agent gave a specific next step with an owner and a timeframe (case number, "billing will email within 2 days", etc.). This is a GOOD outcome.
- unresolved: goal not met and no credible path forward; includes the customer giving up.
Judge by the customer's goal, not by whether the agent said "resolved".

## 3. correctness  -- TIER 1
Compare the agent's substantive claims (policy, timelines, eligibility, what will happen) against the REFERENCE, if one is given.
- supported: every substantive claim is consistent with the reference, AND the action taken matches what the reference prescribes for the customer's situation.
- contradicted: at least one claim or action conflicts with the reference (e.g. reference says wait if under 7 days, agent reships at 3 days; reference says reship at 7+, agent tells a 10-day customer to keep waiting). Cite the turn.
- unverifiable: no reference given, or the reference does not cover the claims made. Do NOT guess. If there is no reference, output unverifiable.
Judge customer-facing claims, outcomes, and observable actions (including SYSTEM action turns such as "Account has been pulled up for X" or "The manager has been notified"). Do NOT penalize a missing internal tool step unless the transcript shows it was skipped — many procedural steps in the reference are internal and leave no visible trace.
Politeness is irrelevant here. A happy customer with a wrong answer is contradicted.

## 4. customer_effort  -- TIER 2  (high = BAD)
Observable effort the customer had to spend:
- low: stated the problem once, provided info once, no transfers, no repeated asks, no unnecessary steps.
- medium: one of: repeated information once, one clarification loop, one avoidable step.
- high: two or more of: repeated information, re-explained the problem, was transferred, was asked for info the agent should already have, was pushed to self-serve after asking for help, gave up.

## 5. interaction_quality  -- TIER 2  (high = GOOD)
Agent behavior, judged on the transcript:
- high: acknowledged the specific problem and its impact before procedure; used the customer's specifics rather than script language; took ownership ("I'll handle this", "reply to me"); clear about what happens next.
- medium: procedurally competent and polite but generic; no acknowledgment of impact, or unclear next steps.
- low: any of: dismissive, blames customer or carrier without helping, deflects ("not our department"), ignores a direct question, hostile, or leaves the customer without a next step.

## CONTEXT (not a quality score)
sentiment_trajectory: for EACH customer turn, a score from -1 (hostile/very upset) to +1 (delighted). Use the turn index. Neutral/transactional = 0.

## needs_human_review
true if any risk flag is medium or high, OR correctness is contradicted. Otherwise false.

## summary
One line (max 20 words) a supervisor can scan in a list: what happened and the single most important thing about it.

# OUTPUT JSON SCHEMA
{
  "risk_flags": [{"type": "<type>", "severity": "low|medium|high", "rationale": "...", "turns": [int]}],
  "resolution": {"level": "resolved|partially_resolved|deferred_with_owner|unresolved", "rationale": "...", "turns": [int]},
  "correctness": {"level": "supported|contradicted|unverifiable", "rationale": "...", "turns": [int]},
  "customer_effort": {"level": "low|medium|high", "rationale": "...", "turns": [int]},
  "interaction_quality": {"level": "low|medium|high", "rationale": "...", "turns": [int]},
  "sentiment_trajectory": [{"turn": int, "score": float}],
  "needs_human_review": bool,
  "summary": "..."
}
"""

# Compact tail used when the caller sends `response_format=json_schema`.
# The provider enforces shape; the prose schema block above is redundant
# and just consumes tokens.
_RUBRIC_STRUCTURED_TAIL = "Return a JSON object matching the provided schema."


def _rubric_body(structured: bool) -> str:
    """Return the rubric body sized to the caller. When `structured=True`,
    strip the "# OUTPUT JSON SCHEMA" prose block (the provider is enforcing
    the shape) and replace with a one-line reminder."""
    if not structured:
        return RUBRIC
    tag = "# OUTPUT JSON SCHEMA"
    idx = RUBRIC.find(tag)
    if idx == -1:
        return RUBRIC
    return RUBRIC[:idx].rstrip() + "\n\n" + _RUBRIC_STRUCTURED_TAIL + "\n"


def _supports_prompt_caching(model: str | None) -> bool:
    """Anthropic recognises `cache_control` blocks on messages; other
    providers ignore or reject them. Only enable caching where it actually
    helps."""
    if not model:
        return False
    m = model.lower()
    return m.startswith("claude") or m.startswith("anthropic/")


def build_messages(transcript_text: str, reference: str | None, intent: str | None,
                   model: str | None = None, structured_output: bool = False) -> list[dict]:
    """Return LiteLLM messages structured so the cacheable prefix (SYSTEM
    plus RUBRIC, and the REFERENCE block) is stable across calls and the
    transcript is always last. On providers that support `cache_control`,
    the prefix is marked ephemeral so a batch of conversations sharing the
    same rubric+reference reuses the cache after the first call.

    When `structured_output=True`, the "# OUTPUT JSON SCHEMA" prose section
    is dropped (the caller is passing `response_format=json_schema` and the
    provider is enforcing the shape) — saves a couple of hundred tokens per
    call. Otherwise the prose schema is included as the fallback path."""
    supports_cache = _supports_prompt_caching(model)
    system_text = SYSTEM + "\n\n" + _rubric_body(structured_output)
    ref_block_text = (
        f"# REFERENCE (policy / guidelines the agent should follow)\n{reference}"
        if reference
        else "# REFERENCE\n(none provided — correctness MUST be 'unverifiable')"
    )
    tail_text = (
        f"# INTENT (from source system, may be absent)\n{intent or '(unknown)'}\n\n"
        f"# TRANSCRIPT\n{transcript_text}\n\nReturn the JSON object now."
    )

    if supports_cache:
        return [
            {"role": "system", "content": [
                {"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}},
            ]},
            {"role": "user", "content": [
                {"type": "text", "text": ref_block_text, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": tail_text},
            ]},
        ]
    # Plain-string form for providers without cache_control.
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": f"{ref_block_text}\n\n{tail_text}"},
    ]


def build_user_prompt(transcript_text: str, reference: str | None, intent: str | None) -> str:
    """Legacy helper: single-string user prompt. Kept for callers that don't
    use the LiteLLM message shape (tests, and any judge that composes prompts
    as strings)."""
    ref_block = reference if reference else "(none provided — correctness MUST be 'unverifiable')"
    return f"""{RUBRIC}

# INTENT (from source system, may be absent)
{intent or "(unknown)"}

# REFERENCE (policy / guidelines the agent should follow)
{ref_block}

# TRANSCRIPT
{transcript_text}

Return the JSON object now."""
