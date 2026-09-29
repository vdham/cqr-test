"""Rubric prompt construction."""
from __future__ import annotations

from cqr.rubric import RUBRIC, RUBRIC_VERSION, SYSTEM, build_messages, build_user_prompt


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


class TestBuildMessagesCaching:
    """Prompt-caching structure: on Anthropic models, system+rubric and the
    reference block carry cache_control ephemeral so batches sharing a
    reference reuse the cache. On other providers, everything falls back to
    plain-string content (LiteLLM won't send cache_control at all)."""

    def test_anthropic_gets_cache_control_on_system(self):
        msgs = build_messages("[0] AGENT: hi", "REF", "shipping_issue/missing",
                              model="claude-sonnet-4-5")
        assert msgs[0]["role"] == "system"
        assert isinstance(msgs[0]["content"], list)
        assert msgs[0]["content"][0].get("cache_control") == {"type": "ephemeral"}
        # system block is SYSTEM + RUBRIC concatenated.
        assert SYSTEM.strip() in msgs[0]["content"][0]["text"]
        assert "SIGNALS" in msgs[0]["content"][0]["text"]  # rubric anchor

    def test_anthropic_reference_precedes_transcript(self):
        msgs = build_messages("[0] AGENT: hi", "MY-REFERENCE", "shipping/missing",
                              model="claude-sonnet-4-5")
        user_content = msgs[1]["content"]
        assert isinstance(user_content, list)
        assert len(user_content) == 2
        # Reference is first (cache_control), transcript tail second (no cache).
        assert user_content[0]["cache_control"] == {"type": "ephemeral"}
        assert "MY-REFERENCE" in user_content[0]["text"]
        assert "cache_control" not in user_content[1]
        assert "TRANSCRIPT" in user_content[1]["text"]
        assert "[0] AGENT: hi" in user_content[1]["text"]

    def test_anthropic_missing_reference_still_structured(self):
        msgs = build_messages("[0] AGENT: hi", None, None,
                              model="claude-sonnet-4-5")
        assert "none provided" in msgs[1]["content"][0]["text"]
        # Reference block is still marked cache_control (fixed blob per run).
        assert msgs[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_non_anthropic_gets_plain_strings(self):
        msgs = build_messages("[0] AGENT: hi", "REF", "shipping/missing",
                              model="gpt-4o")
        assert isinstance(msgs[0]["content"], str)
        assert isinstance(msgs[1]["content"], str)
        # Reference still precedes transcript inside the flattened string.
        user = msgs[1]["content"]
        assert user.index("REF") < user.index("TRANSCRIPT")

    def test_no_model_treated_as_non_anthropic(self):
        msgs = build_messages("[0] AGENT: hi", "REF", None, model=None)
        assert isinstance(msgs[0]["content"], str)
