"""
The model-cannot-override invariant, named.

Every field the schema derives — `needs_human_review`, `sentiment_delta`,
`correctness` when there is no reference, plus the content-address fields
(`schema_version`, `rubric_version`, `transcript_digest`, `reference_version`)
— must be computed after the model's output arrives, regardless of what
the model tried to emit for those fields. This test forces the model to
lie about all of them and asserts the computed Review ignores every lie.

If a future change makes any of these overridable, this test fails and
the CI gate stops the change from landing.
"""
from __future__ import annotations

from cqr.judge import _finalize
from cqr.rubric import RUBRIC_VERSION
from cqr.schema import SCHEMA_VERSION, Transcript, Turn, reference_version


def _fixed_transcript(*, reference: str | None = "REF: reship after 7 days.",
                       intent: str | None = "shipping_issue/missing") -> Transcript:
    return Transcript(
        id="fixed",
        source="upload",
        intent=intent,
        reference=reference,
        turns=[
            Turn(idx=0, speaker="agent", text="Hi."),
            Turn(idx=1, speaker="customer", text="My package is missing."),
            Turn(idx=2, speaker="agent", text="Sorry — I'll ship a replacement."),
            Turn(idx=3, speaker="customer", text="Thanks."),
        ],
    )


def _model_out_with_lies() -> dict:
    """A raw model output where every derived field is a lie the model
    tried to sneak past us."""
    return {
        # Two flags, both medium+ — the schema-derived needs_human_review
        # should therefore be True. The model tries to say False.
        "risk_flags": [
            {"type": "pii_mishandling", "severity": "high",
             "rationale": "agent asked for CVV", "turns": [2]},
            {"type": "unauthorized_promise", "severity": "medium",
             "rationale": "guaranteed delivery", "turns": [2]},
        ],
        "resolution":         {"level": "resolved", "rationale": "r", "turns": [2]},
        "correctness":        {"level": "supported", "rationale": "r", "turns": [2]},
        "customer_effort":    {"level": "low", "rationale": "r", "turns": []},
        "interaction_quality": {"level": "high", "rationale": "r", "turns": [2]},
        "sentiment_trajectory": [{"turn": 1, "score": -0.5}, {"turn": 3, "score": 0.6}],
        "summary": "s",
        # The lies:
        "needs_human_review": False,   # actually True (medium+ risk flags present)
        "sentiment_delta": -99.0,      # actually 1.1 = 0.6 - (-0.5)
        "schema_version": "hacked",    # ignored — computed
        "rubric_version": "hacked",    # ignored — computed
        "transcript_digest": "0" * 64, # ignored — computed
        "reference_version": "hacked", # ignored — computed
    }


class TestModelCannotOverrideDerivedFields:
    def test_needs_human_review_is_recomputed(self):
        r = _finalize(_fixed_transcript(), _model_out_with_lies(), "test")
        assert r.needs_human_review is True  # despite model's False

    def test_sentiment_delta_is_recomputed(self):
        r = _finalize(_fixed_transcript(), _model_out_with_lies(), "test")
        assert r.sentiment_delta == 1.1  # despite model's -99.0

    def test_correctness_forced_unverifiable_when_no_reference(self):
        """Even when the model insists 'supported' or 'contradicted', a
        Transcript with nothing to resolve (no inline reference, no
        reference_id, no matching intent) forces `correctness = unverifiable`."""
        raw = _model_out_with_lies()
        raw["correctness"] = {"level": "contradicted", "rationale": "r", "turns": [2]}
        # No reference AND no intent that resolves — otherwise server-side
        # intent resolution (spec 004) would pull in a canonical reference.
        r = _finalize(_fixed_transcript(reference=None, intent=None), raw, "test")
        assert r.correctness.level.value == "unverifiable"
        # `needs_human_review` therefore drops (no medium+ flags... wait, yes
        # there are two flags in the lies dict). Sanity check separately:
        assert r.needs_human_review is True  # still True because of risk flags


class TestVersioningFieldsAreServerAuthoritative:
    def test_schema_version_is_current(self):
        r = _finalize(_fixed_transcript(), _model_out_with_lies(), "test")
        assert r.schema_version == SCHEMA_VERSION

    def test_rubric_version_is_current(self):
        r = _finalize(_fixed_transcript(), _model_out_with_lies(), "test")
        assert r.rubric_version == RUBRIC_VERSION

    def test_transcript_digest_matches_transcript(self):
        t = _fixed_transcript()
        r = _finalize(t, _model_out_with_lies(), "test")
        assert r.transcript_digest == t.digest()
        assert r.transcript_digest != "0" * 64  # not the model's lie

    def test_reference_version_matches_reference(self):
        t = _fixed_transcript()
        r = _finalize(t, _model_out_with_lies(), "test")
        assert r.reference_version == reference_version(t.reference)
        assert r.reference_version != "hacked"

    def test_reference_version_is_none_string_without_reference(self):
        # No inline reference AND no resolvable intent — resolution 'none'.
        r = _finalize(_fixed_transcript(reference=None, intent=None),
                       _model_out_with_lies(), "test")
        assert r.reference_version == "none"


class TestNoOtherPathToOverride:
    """Even if the model output supplies `warnings` or provenance fields, the
    server-computed versions win."""

    def test_warnings_are_server_authored(self):
        raw = _model_out_with_lies()
        raw["warnings"] = ["model-emitted warning"]
        # A citation the model invented that doesn't map to any real turn —
        # `_finalize` should drop it and add its own warning.
        raw["resolution"]["turns"] = [99]
        r = _finalize(_fixed_transcript(), raw, "test")
        assert r.resolution.turns == []  # 99 stripped
        assert any("resolution" in w and "99" in w for w in r.warnings)
