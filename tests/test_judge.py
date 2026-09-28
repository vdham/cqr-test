"""Judge internals: _finalize, _extract_json, HeuristicJudge, AnthropicJudge, get_judge."""
from __future__ import annotations

import json

import pytest

from cqr.judge import AnthropicJudge, HeuristicJudge, _extract_json, _finalize, get_judge
from cqr.schema import Correctness, Level3, Resolution, RiskFlagType, Transcript, Turn
from tests.conftest import mock_anthropic_client, valid_review_json


# ------------------------------------------------------------ _extract_json ----

class TestExtractJson:
    def test_plain_json(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_code_fence(self):
        assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_bare_code_fence(self):
        assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_prose_wrapped(self):
        assert _extract_json('Here is the JSON: {"a": 1} — enjoy.') == {"a": 1}

    def test_nested_braces(self):
        assert _extract_json('{"a": {"b": 2}}') == {"a": {"b": 2}}

    def test_malformed_raises(self):
        with pytest.raises(json.JSONDecodeError):
            _extract_json("{not json}")


# --------------------------------------------------------------- _finalize ----

class TestFinalize:
    def _raw(self, correctness_level="supported"):
        return {
            "risk_flags": [],
            "resolution": {"level": "resolved", "rationale": "r", "turns": []},
            "correctness": {"level": correctness_level, "rationale": "r", "turns": []},
            "customer_effort": {"level": "low", "rationale": "r", "turns": []},
            "interaction_quality": {"level": "medium", "rationale": "r", "turns": []},
            "sentiment_trajectory": [],
            "summary": "s",
        }

    def test_stamps_provenance_fields(self, sample_transcript):
        r = _finalize(sample_transcript, self._raw(), "anthropic:test-model")
        assert r.transcript_id == sample_transcript.id
        assert r.source == sample_transcript.source
        assert r.intent == sample_transcript.intent
        assert r.judge == "anthropic:test-model"

    def test_no_reference_forces_unverifiable(self, sample_transcript):
        assert sample_transcript.reference is None
        r = _finalize(sample_transcript, self._raw("supported"), "test")
        assert r.correctness.level == "unverifiable"
        assert "reference" in r.correctness.rationale.lower()

    def test_reference_present_preserves_llm_verdict(self, transcript_with_reference):
        r = _finalize(transcript_with_reference, self._raw("contradicted"), "test")
        assert r.correctness.level == "contradicted"
        assert r.needs_human_review is True  # derived by schema

    def test_unverifiable_from_llm_when_no_reference_kept_as_is(self, sample_transcript):
        r = _finalize(sample_transcript, self._raw("unverifiable"), "test")
        assert r.correctness.level == "unverifiable"


# --------------------------------------------------------- HeuristicJudge ----

class TestHeuristicJudge:
    def _judge(self, turns: list[tuple[str, str]]) -> "Review":
        t = Transcript(id="t", source="test",
                       turns=[Turn(idx=i, speaker=s, text=x) for i, (s, x) in enumerate(turns)])
        return HeuristicJudge().judge(t)

    def test_name(self):
        assert HeuristicJudge().name == "heuristic"

    def test_correctness_always_unverifiable(self):
        r = self._judge([("agent", "hi"), ("customer", "help")])
        assert r.correctness.level == "unverifiable"

    def test_detects_pii_mishandling(self):
        r = self._judge([
            ("customer", "I need help with my card."),
            ("agent", "Please share your full 16-digit card number and CVV."),
        ])
        flag_types = [f.type.value for f in r.risk_flags]
        assert "pii_mishandling" in flag_types

    def test_detects_churn_signal(self):
        r = self._judge([
            ("agent", "How can I help?"),
            ("customer", "I'm cancelling and switching to Amazon."),
        ])
        assert any(f.type == RiskFlagType.churn_signal for f in r.risk_flags)

    def test_detects_legal_threat(self):
        r = self._judge([
            ("customer", "I'm calling my lawyer about this."),
            ("agent", "OK."),
        ])
        assert any(f.type == RiskFlagType.legal_or_regulatory for f in r.risk_flags)

    def test_detects_unauthorized_promise(self):
        r = self._judge([
            ("customer", "When will it arrive?"),
            ("agent", "I guarantee it will be there by tomorrow."),
        ])
        assert any(f.type == RiskFlagType.unauthorized_promise for f in r.risk_flags)

    def test_detects_abusive_agent(self):
        r = self._judge([
            ("customer", "It didn't work."),
            ("agent", "Did you even look? That's your choice."),
        ])
        assert any(f.type == RiskFlagType.abusive_agent for f in r.risk_flags)

    def test_resolution_resolved_on_positive_close(self):
        r = self._judge([
            ("customer", "Package missing."),
            ("agent", "I've shipped a replacement, it's on its way."),
        ])
        assert r.resolution.level == Resolution.resolved.value

    def test_resolution_deferred_with_owner(self):
        r = self._judge([
            ("customer", "Package missing."),
            ("agent", "I'll escalate; case #4523 opened, we'll email you within 2 business days."),
        ])
        assert r.resolution.level == Resolution.deferred_with_owner.value

    def test_resolution_unresolved_default(self):
        r = self._judge([
            ("customer", "I have an issue."),
            ("agent", "Hmm, not sure what to do."),
        ])
        assert r.resolution.level == Resolution.unresolved.value

    def test_effort_high_when_multiple_signals(self):
        r = self._judge([
            ("customer", "As I told you already, my order is 123."),
            ("customer", "I already said, the order is 123."),
            ("customer", "Please help."),
            ("customer", "Look at 123."),
            ("customer", "Order 123."),
            ("customer", "One more time: 123."),
            ("customer", "Are you there?"),
            ("agent", "Transferring you to the billing team."),
        ])
        assert r.customer_effort.level == Level3.high.value

    def test_interaction_quality_high_with_ack_and_ownership(self):
        r = self._judge([
            ("customer", "My package didn't come."),
            ("agent", "I'm sorry. I'll handle this personally."),
        ])
        assert r.interaction_quality.level == Level3.high.value

    def test_interaction_quality_low_when_rude(self):
        r = self._judge([
            ("customer", "Please help."),
            ("agent", "Not our department. That's your choice."),
        ])
        assert r.interaction_quality.level == Level3.low.value

    def test_sentiment_trajectory_uses_customer_turns_only(self):
        r = self._judge([
            ("agent", "Hi"),
            ("customer", "Thanks for the help, great service"),
            ("agent", "You're welcome"),
        ])
        assert len(r.sentiment_trajectory) == 1
        assert r.sentiment_trajectory[0].turn == 1
        assert r.sentiment_trajectory[0].score > 0

    def test_summary_included(self):
        r = self._judge([("agent", "hi"), ("customer", "help")])
        assert r.summary.startswith("[heuristic]")


# ------------------------------------------------------------ AnthropicJudge ----

class TestAnthropicJudge:
    def _judge_with_responses(self, sample_transcript, responses):
        j = AnthropicJudge(model="test-model", max_retries=2)
        client, calls = mock_anthropic_client(responses)
        j.client = client
        return j, client, calls

    def test_name_uses_model(self):
        j = AnthropicJudge(model="foo-bar")
        assert j.name == "anthropic:foo-bar"

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("CQR_MODEL", "claude-from-env")
        j = AnthropicJudge()
        assert j.model == "claude-from-env"

    def test_happy_path_first_try(self, sample_transcript):
        j, client, calls = self._judge_with_responses(sample_transcript, [valid_review_json()])
        r = j.judge(sample_transcript)
        assert len(calls) == 1
        assert r.judge == "anthropic:test-model"
        assert r.transcript_id == sample_transcript.id
        assert calls[0]["extra_body"] == {"temperature": 0}

    def test_retries_on_malformed_json(self, sample_transcript):
        j, client, calls = self._judge_with_responses(sample_transcript,
                                                     ["not json at all", valid_review_json()])
        r = j.judge(sample_transcript)
        assert len(calls) == 2
        assert r.transcript_id == sample_transcript.id
        # Second-attempt prompt was extended with error context.
        assert "invalid" in calls[1]["messages"][0]["content"].lower()

    def test_retries_on_validation_error(self, sample_transcript):
        # Missing required signal fields -> ValidationError, then valid.
        j, client, calls = self._judge_with_responses(sample_transcript,
                                                     ['{"risk_flags": []}', valid_review_json()])
        r = j.judge(sample_transcript)
        assert len(calls) == 2
        assert r.transcript_id == sample_transcript.id

    def test_gives_up_after_max_retries(self, sample_transcript):
        j, client, calls = self._judge_with_responses(sample_transcript,
                                                     ["bad", "worse", "worst"])
        with pytest.raises(RuntimeError, match="judge failed after retries"):
            j.judge(sample_transcript)
        assert len(calls) == 3  # 1 initial + 2 retries


# ----------------------------------------------------------------- get_judge ----

class TestGetJudge:
    def test_explicit_heuristic(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        assert get_judge("heuristic").name == "heuristic"

    def test_env_selects_heuristic(self, monkeypatch):
        monkeypatch.setenv("CQR_JUDGE", "heuristic")
        assert get_judge().name == "heuristic"

    def test_default_heuristic_when_no_key(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert get_judge().name == "heuristic"

    def test_default_anthropic_when_key_present(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        # Just check the name; don't actually call the LLM.
        assert get_judge().name.startswith("anthropic:")

    def test_unknown_judge_raises(self):
        with pytest.raises(ValueError, match="unknown judge"):
            get_judge("mystery")
