# Conversation Quality Reviewer — Plan

## Goal
Build a small tool that ingests customer-agent conversation transcripts and surfaces 3–5 quality signals per conversation, exposed via a clear contract (API + CLI). Target ~half a day of effort; sharp and partially-built beats broad and unfocused.

## Stack
- **Python 3.11** + `uv` for env/deps (fast, no Docker overhead).
- **FastAPI** for the HTTP endpoint; also usable as a callable module.
- **Pydantic** for input/output schemas (the "clear contract").
- **Anthropic Claude API** (Haiku 4.5) for LLM-based scoring — cheap, fast, strong at nuance.
- **Rule-based helpers** (regex, keyword lists) for cheap deterministic signals.
- **Streamlit** (stretch) for a demo dashboard on top of the API.

## Data Source
**ABCD dataset** — best domain fit, structured (agent + customer turns, agent actions/intents present for grounding goal-completion). Fallback: hand-generate 10 synthetic transcripts including 2–3 deliberately problematic ones (rude agent, unresolved issue, compliance slip) to make demo signals visible.

Load ~20–30 conversations into a local JSONL for fast iteration.

## Quality Signals (5)
1. **Goal Completion (0–1)** — did the customer's stated issue get resolved? LLM judges against final turns. Most important — a polite unresolved call is still a failure.
2. **Empathy (0–1)** — agent acknowledgment of customer emotion. LLM-scored with rubric (acknowledged feeling, apologized where warranted, personalized).
3. **Customer Sentiment Trajectory (-1 to +1, delta)** — sentiment of last 2 customer turns minus first 2. Captures whether the agent moved the needle, not just static mood.
4. **Compliance Risk (flag + reason)** — regex + LLM check for: sharing full card/SSN, promising unauthorized refunds, missing required disclosures. Binary + short explanation.
5. **Escalation Likelihood (0–1)** — LLM predicts whether this customer will callback/escalate. Signals: unresolved goal, rising frustration, agent hedging.

**Why this set:** covers the four axes reviewers actually care about — *outcome* (goal), *experience* (empathy, sentiment), *risk* (compliance), *forward-looking* (escalation). Sentiment-only tools miss outcome; outcome-only tools miss brand damage.

## Architecture
```
transcripts.jsonl → ingest.py → reviewer.score(convo) → {signals, per_signal_reasons}
                                       ↓
                        FastAPI /review (POST convo → JSON)
                        CLI: python -m reviewer path/to/file.jsonl
```

Each signal is its own function returning `Score(value, reason, evidence_turn_ids)`. Reviewer orchestrator runs them in parallel via `asyncio.gather`. Single LLM call per signal (not one mega-prompt) — easier to iterate rubrics independently and debug.

## Contract (Output Schema)
```json
{
  "conversation_id": "abcd_00123",
  "signals": {
    "goal_completion":   {"score": 0.8, "reason": "...", "evidence": [12, 14]},
    "empathy":           {"score": 0.6, "reason": "...", "evidence": [3]},
    "sentiment_delta":   {"score": 0.3, "reason": "..."},
    "compliance_risk":   {"flag": false, "reason": "..."},
    "escalation_risk":   {"score": 0.2, "reason": "..."}
  },
  "overall": 0.72,
  "model": "claude-haiku-4-5",
  "latency_ms": 1840
}
```

## Milestones (half-day)
1. **30m** — Repo scaffold, load 20 ABCD convos → JSONL, normalize turn schema.
2. **90m** — Implement 5 signal scorers (LLM prompts + rubrics, one compliance regex pass).
3. **45m** — Wire FastAPI `/review` + CLI entrypoint, Pydantic schemas.
4. **30m** — Run on batch, spot-check outputs, tune prompts on 2–3 misfires.
5. **30m** — Write one-page tradeoffs note (metrics choice, prod gaps, eval plan, corners cut).
6. **Stretch** — Streamlit dashboard: table of convos, click to see per-signal reasoning + highlighted turns.

## Corners Cut (call out in tradeoffs note)
- No golden labels / eval set — spot-check only.
- Prompt-based scoring, no fine-tuning or calibration.
- No auth, rate limits, cost tracking on the API.
- Single-model; no ensembling or judge-of-judges.
- Compliance rules are illustrative, not a real policy taxonomy.

## What I'd Harden for Production
Golden eval set with human labels; inter-rater agreement tracking; per-signal calibration; prompt+model versioning; PII redaction pre-LLM; async batch pipeline; cost/latency SLOs; drift monitoring on score distributions.
