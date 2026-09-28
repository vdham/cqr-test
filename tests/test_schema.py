"""Contract-level invariants on Transcript, Turn, and Review."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from cqr.schema import (Correctness, CorrectnessResult, Level3, Level3Result,
                        Resolution, ResolutionResult, Review, RiskFlag, RiskFlagType,
                        SentimentPoint, SignalResult, Transcript, Turn)


# ------------------------------------------------------------- Turn / Transcript ----

class TestTurn:
    def test_speaker_enum_rejects_unknown(self):
        with pytest.raises(ValidationError):
            Turn(idx=0, speaker="bot", text="x")

    def test_accepts_all_three_speakers(self):
        for spk in ("agent", "customer", "system"):
            Turn(idx=0, speaker=spk, text="x")


class TestTranscript:
    def test_render_format(self, sample_transcript):
        out = sample_transcript.render()
        assert out.startswith("[0] AGENT: Hi, how can I help?")
        assert "[1] CUSTOMER: My package never came" in out
        assert out.count("\n") == 3  # 4 turns => 3 newlines

    def test_optional_fields_default(self, sample_transcript):
        assert sample_transcript.intent is None
        assert sample_transcript.reference is None
        assert sample_transcript.metadata == {}

    def test_empty_turns_rejected(self):
        with pytest.raises(ValidationError):
            Transcript(id="x", source="upload", turns=[])

    def test_duplicate_idx_rejected(self):
        with pytest.raises(ValidationError, match="unique"):
            Transcript(id="x", source="upload", turns=[
                Turn(idx=0, speaker="agent", text="hi"),
                Turn(idx=0, speaker="customer", text="?"),
            ])

    def test_non_contiguous_idx_rejected(self):
        with pytest.raises(ValidationError, match="contiguous"):
            Transcript(id="x", source="upload", turns=[
                Turn(idx=0, speaker="agent", text="hi"),
                Turn(idx=2, speaker="customer", text="?"),
            ])

    def test_out_of_order_idx_rejected(self):
        with pytest.raises(ValidationError, match="contiguous"):
            Transcript(id="x", source="upload", turns=[
                Turn(idx=1, speaker="agent", text="hi"),
                Turn(idx=0, speaker="customer", text="?"),
            ])

    def test_all_idx_absent_auto_assigns(self):
        t = Transcript(id="x", source="upload", turns=[
            Turn(speaker="agent", text="hi"),
            Turn(speaker="customer", text="?"),
            Turn(speaker="agent", text="ok"),
        ])
        assert [x.idx for x in t.turns] == [0, 1, 2]

    def test_partial_idx_rejected(self):
        with pytest.raises(ValidationError, match="every turn or none"):
            Transcript(id="x", source="upload", turns=[
                Turn(idx=0, speaker="agent", text="hi"),
                Turn(speaker="customer", text="?"),
            ])


# --------------------------------------------------------------- Review invariants ----

class TestReviewRiskFlagDedupe:
    def test_duplicate_type_severity_merges_turns(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="cvv", turns=[4]),
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="card num", turns=[7]),
        ])
        assert len(r.risk_flags) == 1
        assert r.risk_flags[0].turns == [4, 7]
        assert "cvv" in r.risk_flags[0].rationale
        assert "card num" in r.risk_flags[0].rationale
        assert " | " in r.risk_flags[0].rationale

    def test_same_type_different_severity_stays_split(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="cvv", turns=[4]),
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.low,  rationale="mask", turns=[15]),
        ])
        assert len(r.risk_flags) == 2
        levels = {(f.type.value, f.severity.value) for f in r.risk_flags}
        assert levels == {("pii_mishandling", "high"), ("pii_mishandling", "low")}

    def test_duplicate_rationale_not_repeated(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.churn_signal, severity=Level3.high, rationale="cancel", turns=[3]),
            RiskFlag(type=RiskFlagType.churn_signal, severity=Level3.high, rationale="cancel", turns=[5]),
        ])
        assert r.risk_flags[0].rationale == "cancel"
        assert r.risk_flags[0].turns == [3, 5]

    def test_turns_deduped_and_sorted(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.churn_signal, severity=Level3.high, rationale="r", turns=[7, 4]),
            RiskFlag(type=RiskFlagType.churn_signal, severity=Level3.high, rationale="r", turns=[4, 11]),
        ])
        assert r.risk_flags[0].turns == [4, 7, 11]

    def test_flags_sorted_by_severity_then_type(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.churn_signal,     severity=Level3.medium, rationale="r", turns=[]),
            RiskFlag(type=RiskFlagType.abusive_agent,    severity=Level3.high,   rationale="r", turns=[]),
            RiskFlag(type=RiskFlagType.pii_mishandling,  severity=Level3.high,   rationale="r", turns=[]),
        ])
        types_in_order = [(f.type.value, f.severity.value) for f in r.risk_flags]
        assert types_in_order == [
            ("abusive_agent", "high"),
            ("pii_mishandling", "high"),
            ("churn_signal", "medium"),
        ]

    def test_empty_flags_stays_empty(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[])
        assert r.risk_flags == []


class TestReviewSentimentDerivation:
    def _sp(self, turn, score): return SentimentPoint(turn=turn, score=score)

    def test_trajectory_sorted_by_turn(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, sentiment_trajectory=[
            self._sp(7, 0.8), self._sp(1, -0.4), self._sp(4, 0.0),
        ])
        assert [p.turn for p in r.sentiment_trajectory] == [1, 4, 7]

    def test_delta_is_last_minus_first(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, sentiment_trajectory=[
            self._sp(3, 0.5), self._sp(1, -0.4),  # unsorted; sort should happen first
        ])
        assert r.sentiment_delta == 0.9

    def test_delta_zero_when_fewer_than_two_points(self, minimal_review_kwargs):
        r_empty = Review(**minimal_review_kwargs, sentiment_trajectory=[])
        r_one   = Review(**minimal_review_kwargs, sentiment_trajectory=[self._sp(1, 0.5)])
        assert r_empty.sentiment_delta == 0.0
        assert r_one.sentiment_delta == 0.0

    def test_delta_rounded_to_two_decimals(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, sentiment_trajectory=[
            self._sp(1, -0.333333), self._sp(2, 0.666666),
        ])
        assert r.sentiment_delta == 1.0

    def test_supplied_delta_is_overwritten(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, sentiment_delta=-99.0,
                   sentiment_trajectory=[self._sp(1, 0.0), self._sp(2, 0.5)])
        assert r.sentiment_delta == 0.5


class TestReviewNeedsHumanReview:
    def test_true_when_any_high_flag(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="r", turns=[]),
        ])
        assert r.needs_human_review is True

    def test_true_when_any_medium_flag(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.unauthorized_promise, severity=Level3.medium, rationale="r", turns=[]),
        ])
        assert r.needs_human_review is True

    def test_false_when_only_low_flag(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.low, rationale="r", turns=[]),
        ])
        assert r.needs_human_review is False

    def test_true_when_correctness_contradicted(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["correctness"] = CorrectnessResult(level=Correctness.contradicted, rationale="r", turns=[])
        r = Review(**kw)
        assert r.needs_human_review is True

    def test_false_when_no_flags_and_correctness_supported(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["correctness"] = CorrectnessResult(level=Correctness.supported, rationale="r", turns=[])
        r = Review(**kw)
        assert r.needs_human_review is False

    def test_supplied_needs_review_is_overwritten(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, needs_human_review=True)
        assert r.needs_human_review is False


class TestSortKey:
    def _build(self, base, tid, **overrides):
        kw = {**base, **overrides, "transcript_id": tid}
        return Review(**kw)

    def test_orders_by_max_risk_then_correctness_then_resolution(self, minimal_review_kwargs):
        from cqr.schema import sort_key
        base = dict(minimal_review_kwargs)
        high_risk = self._build(base, "A", risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="r", turns=[]),
        ])
        contradicted = self._build(base, "B",
            correctness=CorrectnessResult(level=Correctness.contradicted, rationale="r", turns=[]))
        unresolved = self._build(base, "C",
            resolution=ResolutionResult(level=Resolution.unresolved, rationale="r", turns=[]))
        clean = self._build(base, "D")
        ordered = sorted([clean, unresolved, contradicted, high_risk], key=sort_key)
        assert [r.transcript_id for r in ordered] == ["A", "B", "C", "D"]


class TestReviewMaxRisk:
    def test_zero_when_no_flags(self, minimal_review_kwargs):
        assert Review(**minimal_review_kwargs).max_risk == 0

    def test_returns_highest_severity(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.churn_signal, severity=Level3.medium, rationale="r", turns=[]),
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="r", turns=[]),
        ])
        assert r.max_risk == 3


class TestReviewLevelTyping:
    """Section A1: SignalResult subclasses reject wrong-enum-family values."""

    def test_resolution_rejects_non_resolution_enum(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["resolution"] = {"level": "supported", "rationale": "r", "turns": []}
        with pytest.raises(ValidationError):
            Review(**kw)

    def test_level3_rejects_out_of_family_value(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["customer_effort"] = {"level": "very high", "rationale": "r", "turns": []}
        with pytest.raises(ValidationError):
            Review(**kw)

    def test_correctness_rejects_bogus_level(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["correctness"] = {"level": "ok", "rationale": "r", "turns": []}
        with pytest.raises(ValidationError):
            Review(**kw)


class TestReviewRubricVersion:
    def test_field_required(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw.pop("rubric_version")
        with pytest.raises(ValidationError):
            Review(**kw)

    def test_value_preserved(self, minimal_review_kwargs):
        kw = dict(minimal_review_kwargs)
        kw["rubric_version"] = "9.9"
        assert Review(**kw).rubric_version == "9.9"


class TestReviewWarnings:
    def test_default_empty(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs)
        assert r.warnings == []

    def test_preserved_when_supplied(self, minimal_review_kwargs):
        r = Review(**minimal_review_kwargs, warnings=["a", "b"])
        assert r.warnings == ["a", "b"]


class TestReviewSerialization:
    def test_round_trip_via_json(self, minimal_review_kwargs):
        original = Review(**minimal_review_kwargs, risk_flags=[
            RiskFlag(type=RiskFlagType.pii_mishandling, severity=Level3.high, rationale="r", turns=[4]),
        ])
        blob = original.model_dump_json()
        restored = Review.model_validate_json(blob)
        assert restored == original
        # Invariants re-applied on restore (idempotent).
        assert restored.needs_human_review is True

    def test_model_validate_dedupes_from_dict(self, minimal_review_kwargs):
        raw = {
            **{k: (v.model_dump() if hasattr(v, "model_dump") else v) for k, v in minimal_review_kwargs.items()},
            "risk_flags": [
                {"type": "churn_signal", "severity": "high", "rationale": "a", "turns": [3]},
                {"type": "churn_signal", "severity": "high", "rationale": "b", "turns": [5]},
            ],
        }
        r = Review.model_validate(raw)
        assert len(r.risk_flags) == 1
        assert r.risk_flags[0].turns == [3, 5]
