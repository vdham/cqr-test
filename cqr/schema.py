"""
The contract. Everything in and out of the reviewer is one of these models.

Input:  Transcript  (a normalized conversation, source-agnostic)
Output: Review      (per-signal levels with rationale + turn citations)
Job types (Job, JobStatus, JobError, BatchAccepted) describe the async
batch protocol; the runtime that fulfils them lives in `cqr/jobs.py`.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


CQR_MAX_BATCH = int(os.environ.get("CQR_MAX_BATCH", "200"))


# ---------------------------------------------------------------- input ----

class Turn(BaseModel):
    idx: Optional[int] = Field(
        default=None,
        description="0-based position in the conversation. If omitted on every turn, the Transcript validator assigns 0..N-1 from list order.",
    )
    speaker: Literal["customer", "agent", "system"]
    text: str


class Transcript(BaseModel):
    id: str
    source: str = Field(description="abcd | synthetic | upload")
    turns: list[Turn] = Field(min_length=1)
    intent: Optional[str] = Field(
        default=None,
        description="Known issue type, if the source provides it (e.g. ABCD subflow). Used to select reference guidelines.",
    )
    reference: Optional[str] = Field(
        default=None,
        description="Policy / guideline text the agent should have followed. Enables the correctness signal. None => correctness is unverifiable.",
    )
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_turn_indices(self):
        """If any turn has an explicit idx, all must; then idx must be unique and
        contiguous 0..N-1 in list order (the evidence-citation contract keys on
        the list position). If every idx is absent, assign 0..N-1 from list order."""
        missing = [i for i, t in enumerate(self.turns) if t.idx is None]
        if missing and len(missing) != len(self.turns):
            raise ValueError("turn idx must be present on every turn or none")
        if missing:
            for i, t in enumerate(self.turns):
                t.idx = i
            return self
        ids = [t.idx for t in self.turns]
        if len(set(ids)) != len(ids):
            raise ValueError(f"turn idx values must be unique; got {ids}")
        if ids != list(range(len(ids))):
            raise ValueError(f"turn idx values must be contiguous 0..N-1 in list order; got {ids}")
        return self

    def render(self) -> str:
        return "\n".join(f"[{t.idx}] {t.speaker.upper()}: {t.text}" for t in self.turns)


# --------------------------------------------------------------- output ----

class Resolution(str, Enum):
    resolved = "resolved"
    partially_resolved = "partially_resolved"
    deferred_with_owner = "deferred_with_owner"   # not done, but a clear next step + owner
    unresolved = "unresolved"


class Correctness(str, Enum):
    supported = "supported"          # agent's substantive claims match the reference
    contradicted = "contradicted"    # at least one claim conflicts with the reference
    unverifiable = "unverifiable"    # no reference covers what was said


class Level3(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class RiskFlagType(str, Enum):
    unauthorized_promise = "unauthorized_promise"   # refund/timeline/discount the agent can't guarantee
    missing_disclosure = "missing_disclosure"       # required disclosure or verification skipped
    pii_mishandling = "pii_mishandling"             # asked for / echoed sensitive data inappropriately
    churn_signal = "churn_signal"                   # cancel, competitor, "never again"
    legal_or_regulatory = "legal_or_regulatory"     # lawyer, lawsuit, regulator, chargeback threat
    safety = "safety"                               # harm, threats, distress
    social_amplification = "social_amplification"  # threatens to post publicly
    abusive_agent = "abusive_agent"                 # agent rude, dismissive, or hostile


class RiskFlag(BaseModel):
    type: RiskFlagType
    severity: Level3
    rationale: str
    turns: list[int] = Field(default_factory=list)


class SignalResult(BaseModel):
    """One scored dimension: a level, why, and where in the transcript to look.

    Subclasses narrow `level` to the specific enum that signal accepts."""
    level: str
    rationale: str = Field(description="One or two sentences. Cite specific behavior, not vibes.")
    turns: list[int] = Field(default_factory=list, description="Turn indices that are the evidence")


class ResolutionResult(SignalResult):
    level: Resolution


class CorrectnessResult(SignalResult):
    level: Correctness


class Level3Result(SignalResult):
    level: Level3


class SentimentPoint(BaseModel):
    turn: int
    score: float = Field(ge=-1, le=1)


_SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _dedupe_risk_flags(flags: list[RiskFlag]) -> list[RiskFlag]:
    """Normalize risk flags to exactly one entry per (type, severity):
    aggregate turns, dedupe-and-join rationales, sort deterministically.

    Grouping by (type, severity) — not just type — preserves the case where the
    same risk recurs at different severities (e.g. a high CVV request and a low
    masked-digit echo), which must remain separate flags."""
    grouped: dict[tuple[str, str], RiskFlag] = {}
    for f in flags:
        key = (f.type.value, f.severity.value)
        if key not in grouped:
            grouped[key] = RiskFlag(type=f.type, severity=f.severity,
                                    rationale=f.rationale, turns=list(f.turns))
            continue
        g = grouped[key]
        for t in f.turns:
            if t not in g.turns:
                g.turns.append(t)
        r = (f.rationale or "").strip()
        if r and r not in g.rationale:
            g.rationale = f"{g.rationale} | {r}" if g.rationale else r
    for g in grouped.values():
        g.turns = sorted(set(g.turns))
    return sorted(grouped.values(),
                  key=lambda g: (_SEV_ORDER.get(g.severity.value, 99), g.type.value))


class Review(BaseModel):
    transcript_id: str
    source: str
    intent: Optional[str] = None

    # Tier 1: must know
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    resolution: ResolutionResult
    correctness: CorrectnessResult

    # Tier 2: explain the experience
    customer_effort: Level3Result           # level: low | medium | high   (high effort = bad)
    interaction_quality: Level3Result       # level: low | medium | high  (high = good)

    # Context (not a quality dimension)
    sentiment_trajectory: list[SentimentPoint] = Field(default_factory=list)
    sentiment_delta: float = Field(default=0.0, description="end minus start; positive = agent moved the customer up. Always present — set by the Review validator.")

    # Provenance
    judge: str = Field(description="which judge produced this: anthropic:<model> | heuristic | llm:<model>")
    rubric_version: str = Field(description="Version of the rubric this review was scored against. Stamped by the judge.")
    job_id: Optional[str] = Field(default=None, description="If this review came from a batch job, the JobRunner id. None for single POST /review calls.")
    needs_human_review: bool = Field(default=False, description="True when any risk flag is medium+ or correctness is contradicted. Always present — set by the Review validator.")
    summary: str = Field(default="", description="One line a supervisor can read in a list view")

    # Diagnostics (server-populated when the judge cites turns it should not have)
    warnings: list[str] = Field(default_factory=list, description="Non-fatal notes produced during finalize (e.g. dropped invalid turn citations).")

    @model_validator(mode="after")
    def _enforce_invariants(self):
        """Contract-level invariants enforced at construction. Any code path that
        builds a Review — judge output, API request, test fixture — obeys these:

        1. risk_flags: exactly one entry per (type, severity), sorted deterministically.
        2. sentiment_trajectory: sorted by turn ascending.
        3. sentiment_delta: derived as last.score - first.score (or 0 if <2 points).
        4. needs_human_review: derived as any medium+ risk flag OR correctness==contradicted.

        Whatever the judge (or caller) supplied for the derived fields is overwritten."""
        self.risk_flags = _dedupe_risk_flags(self.risk_flags)
        self.sentiment_trajectory = sorted(self.sentiment_trajectory, key=lambda p: p.turn)
        self.sentiment_delta = (
            round(self.sentiment_trajectory[-1].score - self.sentiment_trajectory[0].score, 2)
            if len(self.sentiment_trajectory) >= 2 else 0.0
        )
        self.needs_human_review = (
            any(f.severity in (Level3.medium, Level3.high) for f in self.risk_flags)
            or self.correctness.level == Correctness.contradicted
        )
        return self

    @property
    def max_risk(self) -> int:
        order = {"low": 1, "medium": 2, "high": 3}
        return max((order[f.severity.value] for f in self.risk_flags), default=0)


def sort_key(r: Review):
    """Canonical review ordering: riskiest first, then contradicted-correctness,
    then unresolved, then id for stability. Used by both the API and the CLI so
    lists match across surfaces."""
    return (-r.max_risk,
            r.correctness.level != Correctness.contradicted,
            r.resolution.level != Resolution.unresolved,
            r.transcript_id)


class BatchReviewRequest(BaseModel):
    transcripts: list[Transcript] = Field(max_length=CQR_MAX_BATCH)


class BatchReviewResponse(BaseModel):
    reviews: list[Review]
    errors: list[dict] = Field(default_factory=list)


# -------------------------------------------------------------------- jobs ----

class JobStatus(str, Enum):
    queued = "queued"           # accepted, waiting for a worker
    running = "running"         # at least one item picked up
    completed = "completed"     # every item accounted for; may include per-item errors
    failed = "failed"           # config-scope abort (bad key etc.); item work stopped


class JobError(BaseModel):
    transcript_id: str
    error_type: str = Field(description="Exception class name or JudgeError subclass name.")
    message: str
    retryable: bool = Field(default=True, description="True if a resubmit could succeed; controls circuit-breaker counting and dashboard treatment.")
    attempts: int = 1


class Job(BaseModel):
    id: str
    status: JobStatus = JobStatus.queued
    total: int
    completed: int = 0
    errors: list[JobError] = Field(default_factory=list)
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: Optional[datetime] = None


class BatchAccepted(BaseModel):
    """Body of a 202 response to POST /review/batch."""
    job_id: str
    status: JobStatus
    total: int


# --------------------------------------------------------------- errors ----

class ErrorBody(BaseModel):
    """Uniform body for every non-2xx response this API emits, except
    FastAPI's own 422 validation error (which keeps its native
    `{"detail": [...]}` shape). `error_type` tells the client exactly which
    failure fired; `retryable` tells them whether to retry; `retry_after_s`
    mirrors the `Retry-After` header for clients that don't inspect headers."""
    error_type: str = Field(description="Name of the fired class: JudgeUnavailable | JudgeRejected | TranscriptRejected | JudgeOutputInvalid | QueueFull | NotFound | PayloadTooLarge")
    message: str
    retryable: bool
    attempts: Optional[int] = Field(default=None, description="Provider calls made on this item, if the failure came from the judge.")
    retry_after_s: Optional[int] = Field(default=None, description="Present when the response also carries a `Retry-After` header.")
