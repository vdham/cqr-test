"""
The anchored rubric. This is the product: it defines what "good" means.
Levels are anchored to observable behavior so two reviewers (human or model)
land on the same answer, and every level requires turn citations.
"""

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


def build_user_prompt(transcript_text: str, reference: str | None, intent: str | None) -> str:
    ref_block = reference if reference else "(none provided — correctness MUST be 'unverifiable' unless the agent contradicts something universally known)"
    return f"""{RUBRIC}

# INTENT (from source system, may be absent)
{intent or "(unknown)"}

# REFERENCE (policy / guidelines the agent should follow)
{ref_block}

# TRANSCRIPT
{transcript_text}

Return the JSON object now."""
