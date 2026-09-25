"""
The contract. Everything in and out of the reviewer is one of these models.

Input:  Transcript  (a normalized conversation, source-agnostic)
Output: Review      (per-signal levels with rationale + turn citations)
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- input ----

class Turn(BaseModel):
    idx: int = Field(description="0-based position in the conversation")
    speaker: Literal["customer", "agent", "system"]
    text: str


class Transcript(BaseModel):
    id: str
    source: str = Field(description="abcd | synthetic | upload")
    turns: list[Turn]
    intent: Optional[str] = Field(
        default=None,
        description="Known issue type, if the source provides it (e.g. ABCD subflow). Used to select reference guidelines.",
    )
    reference: Optional[str] = Field(
        default=None,
        description="Policy / guideline text the agent should have followed. Enables the correctness signal. None => correctness is unverifiable.",
    )
    metadata: dict = Field(default_factory=dict)

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
    """One scored dimension: a level, why, and where in the transcript to look."""
    level: str
    rationale: str = Field(description="One or two sentences. Cite specific behavior, not vibes.")
    turns: list[int] = Field(default_factory=list, description="Turn indices that are the evidence")


class SentimentPoint(BaseModel):
    turn: int
    score: float = Field(ge=-1, le=1)


class Review(BaseModel):
    transcript_id: str
    source: str
    intent: Optional[str] = None

    # Tier 1: must know
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    resolution: SignalResult
    correctness: SignalResult

    # Tier 2: explain the experience
    customer_effort: SignalResult           # level: low | medium | high   (high effort = bad)
    interaction_quality: SignalResult       # level: low | medium | high  (high = good)

    # Context (not a quality dimension)
    sentiment_trajectory: list[SentimentPoint] = Field(default_factory=list)
    sentiment_delta: float = Field(default=0.0, description="end minus start; positive = agent moved the customer up")

    # Provenance
    judge: str = Field(description="which judge produced this: anthropic:<model> | heuristic")
    needs_human_review: bool = Field(default=False, description="True when any risk flag is medium+ or correctness is contradicted")
    summary: str = Field(default="", description="One line a supervisor can read in a list view")

    @property
    def max_risk(self) -> int:
        order = {"low": 1, "medium": 2, "high": 3}
        return max((order[f.severity.value] for f in self.risk_flags), default=0)


class BatchReviewRequest(BaseModel):
    transcripts: list[Transcript]


class BatchReviewResponse(BaseModel):
    reviews: list[Review]
    errors: list[dict] = Field(default_factory=list)
