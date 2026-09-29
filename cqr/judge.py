"""
Judges turn a Transcript into a Review.

- LLMJudge: LiteLLM-backed LLM-as-judge with the anchored rubric. Production
  path. Any provider LiteLLM speaks — Anthropic, OpenAI, Azure, self-hosted
  OpenAI-compatible gateways — via one interface. Errors from `litellm` are
  classified into the four `JudgeError` subclasses (`cqr/errors.py`) so
  callers get typed failures instead of provider-shaped exceptions.
- HeuristicJudge: keyword/regex baseline. Runs offline with no key, exercises
  the whole pipeline, and doubles as a "what would a naive classifier get
  wrong" comparison in the demo. It is deliberately dumb.

Both return the same Review contract, so the API/UI don't care which ran.
Retry policy is split in two: `num_retries` (transport, handled by LiteLLM)
covers connection resets and 5xx; `max_output_retries` (in this file) covers
the specific case where the provider replied but the JSON didn't validate
against the rubric schema.

Never uses LiteLLM's `fallbacks=` — attribution matters more than availability
for a scoring product, and cross-model quality drift is a bigger risk than
one-off provider outages.
"""
from __future__ import annotations

import copy
import json
import os
import re
from typing import Protocol

from pydantic import ValidationError

from .errors import (JudgeError, JudgeOutputInvalid, JudgeRejected,
                     JudgeUnavailable, TranscriptRejected)
from .rubric import RUBRIC_VERSION, SYSTEM, build_messages, build_user_prompt
from .schema import (Correctness, Level3, Resolution, Review, RiskFlag, RiskFlagType,
                     SentimentPoint, SignalResult, Transcript, Usage)


_SIGNAL_KEYS = ("resolution", "correctness", "customer_effort", "interaction_quality")


def _normalize_level(s: str) -> str:
    """Heal shallow typography variants ('Resolved.', 'CONTRADICTED', ' resolved ').
    Truly invalid levels ('very high', 'ok') still raise ValidationError, which
    is how the output-retry loop is triggered."""
    return s.strip().rstrip(".!?,;:").strip().lower()


class Judge(Protocol):
    name: str
    def judge(self, t: Transcript) -> Review: ...


# --------------------------------------------------------------- helpers ----

def _finalize(t: Transcript, raw: dict, judge_name: str) -> Review:
    """Stamp provenance and turn the LLM's dict into a Review. Three things
    happen here that are strictly out of scope for the schema itself, because
    they depend on the Transcript (which the Review model has no handle on):

    - Normalize level strings so shallow typography ("Resolved.") heals.
    - Force correctness to `unverifiable` when there is no reference.
    - Strip turn citations that don't correspond to a real turn, and sentiment
      points that don't correspond to a CUSTOMER turn. Anything dropped is
      recorded in Review.warnings.

    All other derivations live on Review's model validator."""
    raw = copy.deepcopy(raw)
    raw["transcript_id"] = t.id
    raw["source"] = t.source
    raw["intent"] = t.intent
    raw["judge"] = judge_name
    raw["rubric_version"] = RUBRIC_VERSION

    for key in _SIGNAL_KEYS:
        sig = raw.get(key)
        if isinstance(sig, dict) and isinstance(sig.get("level"), str):
            sig["level"] = _normalize_level(sig["level"])
    for f in raw.get("risk_flags", []) or []:
        if isinstance(f, dict) and isinstance(f.get("severity"), str):
            f["severity"] = _normalize_level(f["severity"])

    if not t.reference and raw.get("correctness", {}).get("level") != Correctness.unverifiable.value:
        raw["correctness"] = {
            "level": Correctness.unverifiable.value,
            "rationale": "No reference policy provided for this conversation; claims cannot be checked.",
            "turns": [],
        }

    valid_idx = {turn.idx for turn in t.turns}
    customer_idx = {turn.idx for turn in t.turns if turn.speaker == "customer"}
    warnings: list[str] = list(raw.get("warnings") or [])

    for key in _SIGNAL_KEYS:
        sig = raw.get(key)
        if isinstance(sig, dict):
            turns = list(sig.get("turns") or [])
            bad = [x for x in turns if x not in valid_idx]
            if bad:
                warnings.append(f"{key}: dropped unknown turn(s) {bad}")
                sig["turns"] = [x for x in turns if x in valid_idx]

    for i, f in enumerate(raw.get("risk_flags", []) or []):
        if isinstance(f, dict):
            turns = list(f.get("turns") or [])
            bad = [x for x in turns if x not in valid_idx]
            if bad:
                label = f.get("type", "?")
                warnings.append(f"risk_flags[{i}] ({label}): dropped unknown turn(s) {bad}")
                f["turns"] = [x for x in turns if x in valid_idx]

    filtered_traj: list[dict] = []
    for p in raw.get("sentiment_trajectory", []) or []:
        if isinstance(p, dict):
            turn = p.get("turn")
            if turn in customer_idx:
                filtered_traj.append(p)
            else:
                warnings.append(f"sentiment_trajectory: dropped non-customer turn {turn}")
    raw["sentiment_trajectory"] = filtered_traj

    raw["warnings"] = warnings
    return Review.model_validate(raw)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


# ------------------------------------------------------------- LLM (LiteLLM) ----

def _classify_litellm_error(exc: Exception, attempts: int) -> JudgeError:
    """Map a litellm exception onto the JudgeError taxonomy. Uses class-name
    tuples via getattr so a missing class in a future litellm version doesn't
    crash on import; unclassified errors fall through as JudgeUnavailable
    (retryable) — the safer default for a "we're not sure" case."""
    import litellm
    name = type(exc).__name__

    def _cls(*names):
        return tuple(c for n in names if (c := getattr(litellm, n, None)) is not None)

    if _cls("AuthenticationError", "PermissionDeniedError", "NotFoundError") \
            and isinstance(exc, _cls("AuthenticationError", "PermissionDeniedError", "NotFoundError")):
        return JudgeRejected(f"{name}: {exc}", attempts=attempts)
    if _cls("BadRequestError", "UnprocessableEntityError", "ContentPolicyViolationError") \
            and isinstance(exc, _cls("BadRequestError", "UnprocessableEntityError", "ContentPolicyViolationError")):
        return TranscriptRejected(f"{name}: {exc}", attempts=attempts)
    return JudgeUnavailable(f"{name}: {exc}", attempts=attempts)


def _extract_usage(msg, model: str) -> Usage:
    """Pull token counts + cost out of a LiteLLM response defensively. Field
    names vary across providers; use getattr and default to 0. Cost lookup
    can fail for gateway/self-hosted models with no pricing table — swallow."""
    import litellm
    u = getattr(msg, "usage", None)
    input_tokens = int(getattr(u, "prompt_tokens", 0) or 0) if u else 0
    output_tokens = int(getattr(u, "completion_tokens", 0) or 0) if u else 0
    cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0
    cache_create = int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0
    try:
        cost = float(litellm.completion_cost(completion_response=msg) or 0.0)
    except Exception:  # noqa: BLE001
        cost = 0.0
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_create,
        cost_usd=round(cost, 6),
    )


class LLMJudge:
    """LiteLLM-backed LLM judge. One provider call per transcript with
    temperature=0. On invalid JSON, appends a fresh user message and retries
    up to `max_output_retries` more times — the cacheable prefix (system +
    reference) stays intact across retries. Never uses litellm's `fallbacks=`."""

    def __init__(self, model: str | None = None, max_output_retries: int = 2):
        self.model = model or os.environ.get("CQR_MODEL", "claude-sonnet-4-5")
        self.max_output_retries = max_output_retries
        self.name = f"llm:{self.model}"

    def judge(self, t: Transcript) -> Review:
        import litellm
        messages = build_messages(t.render(), t.reference, t.intent, model=self.model)
        last_err: Exception | None = None
        for attempt in range(self.max_output_retries + 1):
            try:
                msg = litellm.completion(
                    model=self.model,
                    max_tokens=2000,
                    temperature=0,
                    timeout=float(os.environ.get("CQR_LLM_TIMEOUT_S", "60")),
                    num_retries=int(os.environ.get("CQR_LLM_RETRIES", "2")),
                    api_base=os.environ.get("CQR_LLM_BASE_URL") or None,
                    messages=messages,
                )
            except Exception as exc:
                raise _classify_litellm_error(exc, attempts=attempt + 1) from exc

            text = msg.choices[0].message.content or ""
            try:
                review = _finalize(t, _extract_json(text), self.name)
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = e
                messages = messages + [{
                    "role": "user",
                    "content": (
                        f"Your previous output was invalid "
                        f"({type(e).__name__}: {str(e)[:300]}). "
                        "Return ONLY valid JSON matching the schema."
                    ),
                }]
                continue
            review.usage = _extract_usage(msg, self.model)
            return review
        raise JudgeOutputInvalid(
            f"invalid JSON after {self.max_output_retries} output retries: {last_err}",
            attempts=self.max_output_retries + 1,
        )


# ------------------------------------------------------------- Heuristic ----

_NEG = re.compile(r"\b(ridiculous|joke|useless|unbelievable|terrible|worst|angry|furious|unacceptable|frustrat|waste|never again|ten days|twelve days)\b|!{2,}", re.I)
_SHOUT = re.compile(r"\b[A-Z]{4,}\b")
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

        if _RESOLVED.search(all_a) and not re.search(r"useless|unbelievable", all_c, re.I):
            res = Resolution.resolved
        elif _DEFERRED.search(all_a):
            res = Resolution.deferred_with_owner
        else:
            res = Resolution.unresolved
        res_turns = [x.idx for x in agent if _RESOLVED.search(x.text) or _DEFERRED.search(x.text)]

        effort_hits = []
        if re.search(r"transferr?ing|this is the .* team", all_a, re.I): effort_hits.append("transfer")
        if re.search(r"as i (told|said)|already (gave|said|told)|i gave .* already", all_c, re.I): effort_hits.append("re-explained")
        if len(cust) > 6: effort_hits.append("long")
        effort = Level3.high if len(effort_hits) >= 2 else Level3.medium if effort_hits else Level3.low

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
    """Resolve the concrete judge. Priority: explicit arg > `CQR_JUDGE` env >
    llm if `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`CQR_LLM_BASE_URL` is set,
    else heuristic. `anthropic` is accepted as a back-compat alias for `llm`."""
    name = name or os.environ.get("CQR_JUDGE") or (
        "llm" if (os.environ.get("ANTHROPIC_API_KEY")
                  or os.environ.get("OPENAI_API_KEY")
                  or os.environ.get("CQR_LLM_BASE_URL"))
        else "heuristic"
    )
    if name in ("llm", "anthropic"):
        return LLMJudge()
    if name == "heuristic":
        return HeuristicJudge()
    raise ValueError(f"unknown judge {name}")
