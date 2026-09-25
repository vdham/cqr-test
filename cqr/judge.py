"""
Judges turn a Transcript into a Review.

- AnthropicJudge: LLM-as-judge with the anchored rubric. The real thing.
- HeuristicJudge: keyword/regex baseline. Runs offline with no key, exercises the
  whole pipeline, and doubles as a "what would a naive classifier get wrong"
  comparison in the demo. It is deliberately dumb.

Both return the same Review contract, so the API/UI don't care which ran.
"""
from __future__ import annotations

import json
import os
import re
from typing import Protocol

from pydantic import ValidationError

from .rubric import SYSTEM, build_user_prompt
from .schema import (Correctness, Level3, Resolution, Review, RiskFlag, RiskFlagType,
                     SentimentPoint, SignalResult, Transcript)


class Judge(Protocol):
    name: str
    def judge(self, t: Transcript) -> Review: ...


# --------------------------------------------------------------- helpers ----

def _finalize(t: Transcript, raw: dict, judge_name: str) -> Review:
    """Validate the judge's dict against the contract and derive the fields we
    never trust the judge to compute (delta, needs_human_review)."""
    raw = dict(raw)
    raw["transcript_id"] = t.id
    raw["source"] = t.source
    raw["intent"] = t.intent
    raw["judge"] = judge_name
    # Correctness without a reference is unverifiable, whatever the judge said.
    if not t.reference and raw.get("correctness", {}).get("level") != Correctness.unverifiable.value:
        raw["correctness"] = {
            "level": Correctness.unverifiable.value,
            "rationale": "No reference policy provided for this conversation; claims cannot be checked.",
            "turns": [],
        }
    review = Review.model_validate(raw)

    traj = sorted(review.sentiment_trajectory, key=lambda p: p.turn)
    review.sentiment_trajectory = traj
    review.sentiment_delta = round(traj[-1].score - traj[0].score, 2) if len(traj) >= 2 else 0.0
    review.needs_human_review = (
        any(f.severity in (Level3.medium, Level3.high) for f in review.risk_flags)
        or review.correctness.level == Correctness.contradicted.value
    )
    return review


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ------------------------------------------------------------- Anthropic ----

class AnthropicJudge:
    def __init__(self, model: str | None = None, max_retries: int = 2):
        import anthropic  # local import so the heuristic path has no dependency
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("CQR_MODEL", "claude-sonnet-4-5")
        self.max_retries = max_retries
        self.name = f"anthropic:{self.model}"

    def judge(self, t: Transcript) -> Review:
        prompt = build_user_prompt(t.render(), t.reference, t.intent)
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=2000,
                temperature=0,
                system=SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
            try:
                return _finalize(t, _extract_json(text), self.name)
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = e
                prompt = prompt + f"\n\nYour previous output was invalid ({type(e).__name__}: {str(e)[:300]}). Return ONLY valid JSON matching the schema."
        raise RuntimeError(f"judge failed after retries for {t.id}: {last_err}")


# ------------------------------------------------------------- Heuristic ----

_NEG = re.compile(r"\b(ridiculous|joke|useless|unbelievable|terrible|worst|angry|furious|unacceptable|frustrat|waste|never again|ten days|twelve days)\b|!{2,}", re.I)
_SHOUT = re.compile(r"\b[A-Z]{4,}\b")  # case-sensitive on purpose
_POS = re.compile(r"\b(thank|thanks|great|perfect|helpful|awesome|appreciate|works now|easy)\b", re.I)
_CHURN = re.compile(r"\b(cancel(l)?(ing)?\b|switch(ing)? to|going to (amazon|zappos|a competitor)|never (buy|order|shop)|take my business)\b", re.I)
_SOCIAL = re.compile(r"\b(twitter|x\.com|reddit|yelp|review|post(ing)? this|social media|press)\b", re.I)
_LEGAL = re.compile(r"\b(lawyer|attorney|lawsuit|sue|chargeback|bbb|regulator|legal)\b", re.I)
_PII_REQ = re.compile(r"\b(full|16.digit|entire).{0,20}(card|account) number|\bcvv\b|\bssn\b|social security|type .{0,30}password", re.I)
_PROMISE = re.compile(r"\b(guarantee|by tomorrow|within the hour|today for sure|I promise)\b", re.I)
_RUDE = re.compile(r"\b(did you (even )?look|literally|are we done|not (my|our) (problem|department)|that'?s your choice|handled by the carrier, not us)\b", re.I)
_RESOLVED = re.compile(r"\b(done|resolved|on its way|has been (reset|updated|shipped|sent)|it works now|you'?ll (see|receive)|i'?ve (reset|updated|shipped|initiated))\b", re.I)
_DEFERRED = re.compile(r"\b(case ?#?\d+|ticket|within \d+ (business )?days|will (email|contact|call) you|escalat)\b", re.I)
_OWNERSHIP = re.compile(r"\b(i'?ll (handle|fix|take care|sort)|i can fix|straight to me|reply to (me|that email)|i'?ve (opened|added a note))\b", re.I)
_ACK = re.compile(r"\b(sorry|i'?m sorry|not okay|that would worry me|i understand|apolog)\b", re.I)


class HeuristicJudge:
    """Regex baseline. Exists so the pipeline runs with no API key and so the
    demo can show where a naive approach falls down (it cannot do correctness)."""
    name = "heuristic"

    def judge(self, t: Transcript) -> Review:
        cust = [x for x in t.turns if x.speaker == "customer"]
        agent = [x for x in t.turns if x.speaker == "agent"]
        all_c = " ".join(x.text for x in cust)
        all_a = " ".join(x.text for x in agent)

        flags: list[RiskFlag] = []
        def flag(kind, sev, why, turns):
            flags.append(RiskFlag(type=kind, severity=sev, rationale=why, turns=turns))

        for pat, kind, sev, who in [
            (_CHURN, RiskFlagType.churn_signal, Level3.high, cust),
            (_SOCIAL, RiskFlagType.social_amplification, Level3.medium, cust),
            (_LEGAL, RiskFlagType.legal_or_regulatory, Level3.high, cust),
            (_PII_REQ, RiskFlagType.pii_mishandling, Level3.high, agent),
            (_PROMISE, RiskFlagType.unauthorized_promise, Level3.medium, agent),
            (_RUDE, RiskFlagType.abusive_agent, Level3.high, agent),
        ]:
            hits = [x.idx for x in who if pat.search(x.text)]
            if hits:
                flag(kind, sev, f"keyword match: {pat.pattern[:40]}...", hits)

        # resolution
        if _RESOLVED.search(all_a) and not re.search(r"useless|unbelievable", all_c, re.I):
            res = Resolution.resolved
        elif _DEFERRED.search(all_a):
            res = Resolution.deferred_with_owner
        else:
            res = Resolution.unresolved
        res_turns = [x.idx for x in agent if _RESOLVED.search(x.text) or _DEFERRED.search(x.text)]

        # effort: count repeats / transfer / "as I told"
        effort_hits = []
        if re.search(r"transferr?ing|this is the .* team", all_a, re.I): effort_hits.append("transfer")
        if re.search(r"as i (told|said)|already (gave|said|told)|i gave .* already", all_c, re.I): effort_hits.append("re-explained")
        if len(cust) > 6: effort_hits.append("long")
        effort = Level3.high if len(effort_hits) >= 2 else Level3.medium if effort_hits else Level3.low

        # interaction quality
        q_score = 0
        if _ACK.search(all_a): q_score += 1
        if _OWNERSHIP.search(all_a): q_score += 1
        if _RUDE.search(all_a): q_score -= 2
        iq = Level3.high if q_score >= 2 else Level3.low if q_score < 0 else Level3.medium

        traj = []
        for x in cust:
            s = 0.0
            if _NEG.search(x.text) or _SHOUT.search(x.text): s -= 0.6
            if _POS.search(x.text): s += 0.6
            traj.append(SentimentPoint(turn=x.idx, score=max(-1, min(1, s))))

        raw = {
            "risk_flags": [f.model_dump() for f in flags],
            "resolution": SignalResult(level=res.value, rationale=f"keyword heuristic; matched agent turns {res_turns}", turns=res_turns).model_dump(),
            "correctness": SignalResult(level=Correctness.unverifiable.value, rationale="heuristic judge cannot check claims against a reference", turns=[]).model_dump(),
            "customer_effort": SignalResult(level=effort.value, rationale="signals: " + (", ".join(effort_hits) or "none"), turns=[]).model_dump(),
            "interaction_quality": SignalResult(level=iq.value, rationale=f"ack/ownership/rudeness score={q_score}", turns=[]).model_dump(),
            "sentiment_trajectory": [p.model_dump() for p in traj],
            "needs_human_review": False,
            "summary": f"[heuristic] {res.value}, {len(flags)} flag(s), effort {effort.value}",
        }
        return _finalize(t, raw, self.name)


def get_judge(name: str | None = None) -> Judge:
    name = name or os.environ.get("CQR_JUDGE") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic")
    if name == "anthropic":
        return AnthropicJudge()
    if name == "heuristic":
        return HeuristicJudge()
    raise ValueError(f"unknown judge {name}")
