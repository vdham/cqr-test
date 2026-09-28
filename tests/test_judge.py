"""Judge internals: _finalize, _extract_json, HeuristicJudge, LLMJudge, get_judge."""
from __future__ import annotations

import json

import pytest

from cqr.errors import (JudgeError, JudgeOutputInvalid, JudgeRejected,
                        JudgeUnavailable, TranscriptRejected)
from cqr.judge import (HeuristicJudge, LLMJudge, _classify_litellm_error,
                       _extract_json, _finalize, get_judge)
from cqr.schema import Correctness, Level3, Resolution, RiskFlagType, Transcript, Turn
from tests.conftest import valid_review_json


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
        from cqr.rubric import RUBRIC_VERSION
        assert r.rubric_version == RUBRIC_VERSION

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


class TestFinalizeLevelNormalization:
    """Section A1: healable typography ('Resolved.', 'CONTRADICTED') normalizes
    without triggering the retry loop; genuinely invalid levels ('very high',
    'ok') raise ValidationError so the retry actually fires."""

    def _raw_with(self, **level_overrides):
        base = {
            "resolution": {"level": "resolved", "rationale": "r", "turns": []},
            "correctness": {"level": "supported", "rationale": "r", "turns": []},
            "customer_effort": {"level": "low", "rationale": "r", "turns": []},
            "interaction_quality": {"level": "medium", "rationale": "r", "turns": []},
            "sentiment_trajectory": [], "summary": "s",
        }
        for k, v in level_overrides.items():
            base[k]["level"] = v
        return base

    def test_trailing_period_healed(self, transcript_with_reference):
        r = _finalize(transcript_with_reference,
                      self._raw_with(resolution="Resolved."), "t")
        assert r.resolution.level == "resolved"

    def test_uppercase_healed(self, transcript_with_reference):
        r = _finalize(transcript_with_reference,
                      self._raw_with(correctness="CONTRADICTED"), "t")
        assert r.correctness.level == "contradicted"
        assert r.needs_human_review is True  # derived from the healed level

    def test_whitespace_healed(self, transcript_with_reference):
        r = _finalize(transcript_with_reference,
                      self._raw_with(customer_effort="  high  "), "t")
        assert r.customer_effort.level == "high"

    def test_invalid_level_raises(self, transcript_with_reference):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _finalize(transcript_with_reference,
                      self._raw_with(customer_effort="very high"), "t")

    def test_bogus_level_raises(self, transcript_with_reference):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _finalize(transcript_with_reference,
                      self._raw_with(interaction_quality="ok"), "t")

    def test_risk_flag_severity_also_normalized(self, transcript_with_reference):
        raw = self._raw_with()
        raw["risk_flags"] = [{"type": "churn_signal", "severity": "HIGH", "rationale": "r", "turns": []}]
        r = _finalize(transcript_with_reference, raw, "t")
        assert r.risk_flags[0].severity.value == "high"


class TestFinalizeCitationValidation:
    """Section A2: turn citations outside the transcript are silently stripped
    with a warning; sentiment points for non-customer turns are stripped too."""

    def _raw(self):
        return {
            "resolution": {"level": "resolved", "rationale": "r", "turns": []},
            "correctness": {"level": "supported", "rationale": "r", "turns": []},
            "customer_effort": {"level": "low", "rationale": "r", "turns": []},
            "interaction_quality": {"level": "medium", "rationale": "r", "turns": []},
            "sentiment_trajectory": [], "summary": "s",
        }

    def test_out_of_range_signal_turn_dropped_with_warning(self, transcript_with_reference):
        # sample_transcript has 4 turns (idx 0..3); 99 is out of range.
        raw = self._raw()
        raw["resolution"]["turns"] = [0, 99, 3]
        r = _finalize(transcript_with_reference, raw, "t")
        assert 99 not in r.resolution.turns
        assert set(r.resolution.turns) == {0, 3}
        assert any("resolution" in w and "99" in w for w in r.warnings)

    def test_risk_flag_turn_out_of_range_dropped(self, transcript_with_reference):
        raw = self._raw()
        raw["risk_flags"] = [{"type": "churn_signal", "severity": "high",
                              "rationale": "r", "turns": [1, 42]}]
        r = _finalize(transcript_with_reference, raw, "t")
        assert r.risk_flags[0].turns == [1]
        assert any("risk_flags" in w and "42" in w for w in r.warnings)

    def test_agent_turn_stripped_from_sentiment(self, transcript_with_reference):
        # sample_transcript idx 0 and 2 are AGENT; 1 and 3 are CUSTOMER.
        raw = self._raw()
        raw["sentiment_trajectory"] = [
            {"turn": 0, "score": 0.1},   # agent -> drop
            {"turn": 1, "score": -0.3},  # customer -> keep
            {"turn": 3, "score": 0.5},   # customer -> keep
        ]
        r = _finalize(transcript_with_reference, raw, "t")
        assert [p.turn for p in r.sentiment_trajectory] == [1, 3]
        assert r.sentiment_delta == round(0.5 - (-0.3), 2)  # recomputed from survivors
        assert any("sentiment_trajectory" in w and "0" in w for w in r.warnings)

    def test_unknown_sentiment_turn_dropped(self, transcript_with_reference):
        raw = self._raw()
        raw["sentiment_trajectory"] = [{"turn": 99, "score": 0.5}]
        r = _finalize(transcript_with_reference, raw, "t")
        assert r.sentiment_trajectory == []
        assert any("sentiment_trajectory" in w for w in r.warnings)

    def test_clean_citations_produce_no_warnings(self, transcript_with_reference):
        raw = self._raw()
        raw["resolution"]["turns"] = [3]
        raw["sentiment_trajectory"] = [{"turn": 1, "score": 0.0}, {"turn": 3, "score": 0.5}]
        r = _finalize(transcript_with_reference, raw, "t")
        assert r.warnings == []


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


# --------------------------------------------------------------- LLMJudge ----

from types import SimpleNamespace


def _make_litellm_exc(cls_name: str):
    """Instantiate a litellm exception for tests. Constructor signatures vary
    between subclasses (some want a real httpx.Response); we skip __init__
    entirely and inject the attributes that different litellm __str__ methods
    read from, so isinstance checks and str(exc) both work regardless."""
    import litellm
    cls = getattr(litellm, cls_name)
    exc = cls.__new__(cls)
    exc.message = f"synthetic {cls_name} for tests"
    for attr in ("num_retries", "max_retries", "llm_provider", "model",
                 "litellm_debug_info", "body", "detail", "response"):
        if not hasattr(exc, attr):
            setattr(exc, attr, None)
    return exc


def _mock_litellm_completion(responses):
    """Build a fake `litellm.completion` that returns pre-canned response
    strings in order. Each entry can be either a plain string (the model's
    text output) or an Exception instance (which is raised)."""
    calls: list[dict] = []

    def completion(**kwargs):
        calls.append(kwargs)
        item = responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(content=item)),
        ])

    return completion, calls


class TestLLMJudge:
    def _install_mock(self, monkeypatch, responses):
        completion, calls = _mock_litellm_completion(responses)
        import litellm
        monkeypatch.setattr(litellm, "completion", completion)
        return calls

    def test_name_uses_model(self):
        j = LLMJudge(model="my-model")
        assert j.name == "llm:my-model"

    def test_model_from_env(self, monkeypatch):
        monkeypatch.setenv("CQR_MODEL", "claude-from-env")
        j = LLMJudge()
        assert j.model == "claude-from-env"

    def test_happy_path_first_try(self, sample_transcript, monkeypatch):
        calls = self._install_mock(monkeypatch, [valid_review_json()])
        j = LLMJudge(model="test-model")
        r = j.judge(sample_transcript)
        assert len(calls) == 1
        assert r.judge == "llm:test-model"
        assert calls[0]["temperature"] == 0
        assert calls[0]["model"] == "test-model"
        # No fallbacks kwarg is EVER sent — attribution matters.
        assert "fallbacks" not in calls[0]

    def test_api_base_from_env(self, sample_transcript, monkeypatch):
        monkeypatch.setenv("CQR_LLM_BASE_URL", "https://gateway.example.com")
        self._install_mock(monkeypatch, [valid_review_json()])
        j = LLMJudge(model="test-model")
        r = j.judge(sample_transcript)
        # Verify api_base was passed through.
        import litellm
        # Re-invoke via patched completion and inspect via a fresh mock:
        calls = self._install_mock(monkeypatch, [valid_review_json()])
        LLMJudge(model="test-model").judge(sample_transcript)
        assert calls[0]["api_base"] == "https://gateway.example.com"

    def test_output_retry_on_invalid_json(self, sample_transcript, monkeypatch):
        calls = self._install_mock(monkeypatch, ["not json at all", valid_review_json()])
        r = LLMJudge(model="test-model").judge(sample_transcript)
        assert len(calls) == 2
        assert "invalid" in calls[1]["messages"][-1]["content"].lower()
        assert r.transcript_id == sample_transcript.id

    def test_output_retry_on_validation_error(self, sample_transcript, monkeypatch):
        calls = self._install_mock(monkeypatch, ['{"risk_flags": []}', valid_review_json()])
        r = LLMJudge(model="test-model").judge(sample_transcript)
        assert len(calls) == 2
        assert r.transcript_id == sample_transcript.id

    def test_gives_up_after_output_retries(self, sample_transcript, monkeypatch):
        calls = self._install_mock(monkeypatch, ["bad", "worse", "worst"])
        with pytest.raises(JudgeOutputInvalid) as exc:
            LLMJudge(model="test-model", max_output_retries=2).judge(sample_transcript)
        assert len(calls) == 3
        assert exc.value.attempts == 3
        assert exc.value.retryable is False
        assert exc.value.http_status == 500

    def test_temperature_zero_and_max_tokens_passed(self, sample_transcript, monkeypatch):
        calls = self._install_mock(monkeypatch, [valid_review_json()])
        LLMJudge(model="m").judge(sample_transcript)
        assert calls[0]["temperature"] == 0
        assert calls[0]["max_tokens"] == 2000

    def test_num_retries_and_timeout_from_env(self, sample_transcript, monkeypatch):
        monkeypatch.setenv("CQR_LLM_TIMEOUT_S", "45.5")
        monkeypatch.setenv("CQR_LLM_RETRIES", "7")
        calls = self._install_mock(monkeypatch, [valid_review_json()])
        LLMJudge(model="m").judge(sample_transcript)
        assert calls[0]["timeout"] == 45.5
        assert calls[0]["num_retries"] == 7


class TestClassifyLiteLLMError:
    """Every provider-shaped exception maps to the right JudgeError subclass."""

    def _make(self, cls_name: str):
        """Construct a real litellm exception with the minimum required args.
        Falls back to attribute injection for classes whose __init__ signatures
        vary between litellm versions."""
        return _make_litellm_exc(cls_name)

    def test_auth_error_becomes_judge_rejected(self):
        exc = self._make("AuthenticationError")
        classified = _classify_litellm_error(exc, attempts=1)
        assert isinstance(classified, JudgeRejected)
        assert classified.http_status == 502
        assert classified.scope == "config"
        assert classified.retryable is False

    def test_permission_denied_becomes_judge_rejected(self):
        exc = self._make("PermissionDeniedError")
        assert isinstance(_classify_litellm_error(exc, attempts=1), JudgeRejected)

    def test_not_found_becomes_judge_rejected(self):
        exc = self._make("NotFoundError")
        assert isinstance(_classify_litellm_error(exc, attempts=1), JudgeRejected)

    def test_bad_request_becomes_transcript_rejected(self):
        exc = self._make("BadRequestError")
        classified = _classify_litellm_error(exc, attempts=1)
        assert isinstance(classified, TranscriptRejected)
        assert classified.http_status == 422
        assert classified.scope == "transcript"

    def test_content_policy_becomes_transcript_rejected(self):
        exc = self._make("ContentPolicyViolationError")
        assert isinstance(_classify_litellm_error(exc, attempts=1), TranscriptRejected)

    def test_rate_limit_becomes_judge_unavailable(self):
        exc = self._make("RateLimitError")
        classified = _classify_litellm_error(exc, attempts=1)
        assert isinstance(classified, JudgeUnavailable)
        assert classified.retryable is True
        assert classified.http_status == 503

    def test_timeout_becomes_judge_unavailable(self):
        exc = self._make("Timeout")
        assert isinstance(_classify_litellm_error(exc, attempts=1), JudgeUnavailable)

    def test_api_connection_becomes_judge_unavailable(self):
        exc = self._make("APIConnectionError")
        assert isinstance(_classify_litellm_error(exc, attempts=1), JudgeUnavailable)

    def test_internal_server_becomes_judge_unavailable(self):
        exc = self._make("InternalServerError")
        assert isinstance(_classify_litellm_error(exc, attempts=1), JudgeUnavailable)

    def test_unknown_exception_defaults_to_judge_unavailable(self):
        classified = _classify_litellm_error(RuntimeError("mystery"), attempts=2)
        assert isinstance(classified, JudgeUnavailable)
        assert classified.attempts == 2


class TestLLMJudgeErrorPropagation:
    """The judge raises the correct JudgeError subclass for each provider error."""

    def _install(self, monkeypatch, exc):
        import litellm
        def _boom(**kwargs):
            raise exc
        monkeypatch.setattr(litellm, "completion", _boom)

    def _make(self, cls_name: str):
        return _make_litellm_exc(cls_name)

    def test_auth_error_raises_judge_rejected(self, sample_transcript, monkeypatch):
        self._install(monkeypatch, self._make("AuthenticationError"))
        with pytest.raises(JudgeRejected):
            LLMJudge(model="m").judge(sample_transcript)

    def test_rate_limit_raises_judge_unavailable(self, sample_transcript, monkeypatch):
        self._install(monkeypatch, self._make("RateLimitError"))
        with pytest.raises(JudgeUnavailable):
            LLMJudge(model="m").judge(sample_transcript)

    def test_bad_request_raises_transcript_rejected(self, sample_transcript, monkeypatch):
        self._install(monkeypatch, self._make("BadRequestError"))
        with pytest.raises(TranscriptRejected):
            LLMJudge(model="m").judge(sample_transcript)


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
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CQR_LLM_BASE_URL", raising=False)
        assert get_judge().name == "heuristic"

    def test_default_llm_when_anthropic_key_present(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert get_judge().name.startswith("llm:")

    def test_default_llm_when_openai_key_present(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert get_judge().name.startswith("llm:")

    def test_default_llm_when_base_url_set(self, monkeypatch):
        monkeypatch.delenv("CQR_JUDGE", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("CQR_LLM_BASE_URL", "https://gateway.example.com")
        assert get_judge().name.startswith("llm:")

    def test_anthropic_alias_returns_llm(self, monkeypatch):
        # Back-compat: old callers passing "anthropic" still get an LLMJudge.
        j = get_judge("anthropic")
        assert j.name.startswith("llm:")

    def test_unknown_judge_raises(self):
        with pytest.raises(ValueError, match="unknown judge"):
            get_judge("mystery")
