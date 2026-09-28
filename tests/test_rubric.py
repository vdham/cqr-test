"""Rubric prompt construction."""
from __future__ import annotations

from cqr.rubric import RUBRIC, RUBRIC_VERSION, SYSTEM, build_user_prompt


class TestBuildUserPrompt:
    def test_includes_rubric_and_transcript(self):
        p = build_user_prompt("[0] AGENT: hi", None, None)
        assert "SIGNALS" in p  # rubric header
        assert "[0] AGENT: hi" in p
        assert "Return the JSON object now." in p

    def test_missing_reference_shows_fallback_note(self):
        p = build_user_prompt("...", reference=None, intent="x")
        assert "(none provided" in p
        assert "unverifiable" in p

    def test_missing_reference_no_universally_known_clause(self):
        # C1: prompt and _finalize invariant must agree — no-reference always
        # means unverifiable. The old "unless universally known" escape hatch
        # let the LLM contradict the invariant.
        p = build_user_prompt("...", reference=None, intent=None)
        assert "universally known" not in p

    def test_reference_present_included_verbatim(self):
        ref = "AGENT GUIDELINES: reship after 7 days."
        p = build_user_prompt("...", reference=ref, intent=None)
        assert ref in p

    def test_missing_intent_shows_unknown(self):
        p = build_user_prompt("...", None, None)
        assert "(unknown)" in p

    def test_intent_included_when_present(self):
        p = build_user_prompt("...", None, intent="shipping_issue/missing")
        assert "shipping_issue/missing" in p


class TestSystemPrompt:
    def test_says_json_only(self):
        assert "JSON" in SYSTEM
        assert "prose" in SYSTEM.lower()

    def test_demands_citations(self):
        assert "cite" in SYSTEM.lower() or "citation" in SYSTEM.lower() or "turn" in SYSTEM.lower()


class TestRubricContent:
    def test_all_five_signals_present(self):
        for s in ("risk_flags", "resolution", "correctness", "customer_effort",
                  "interaction_quality"):
            assert s in RUBRIC

    def test_deterministic_emission_rule_present(self):
        # The rule we added: one flag per (type, severity).
        assert "(type, severity)" in RUBRIC

    def test_correctness_scoped_to_observable(self):
        # C1: correctness §3 must instruct the judge to treat internal tool
        # steps as unverifiable rather than penalize their absence.
        assert "SYSTEM" in RUBRIC
        assert "internal tool step" in RUBRIC


class TestRubricVersion:
    def test_is_a_semver_style_string(self):
        assert isinstance(RUBRIC_VERSION, str)
        parts = RUBRIC_VERSION.split(".")
        assert all(p.isdigit() for p in parts)
