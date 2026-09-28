"""Shared fixtures for the CQR test suite."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cqr.schema import (Correctness, CorrectnessResult, Level3, Level3Result,
                        Resolution, ResolutionResult, RiskFlag, RiskFlagType,
                        SignalResult, Transcript, Turn)
from cqr.store import Store


@pytest.fixture
def make_turn():
    return lambda idx, spk, text: Turn(idx=idx, speaker=spk, text=text)


@pytest.fixture
def sample_transcript(make_turn):
    return Transcript(
        id="conv-1",
        source="test",
        turns=[
            make_turn(0, "agent", "Hi, how can I help?"),
            make_turn(1, "customer", "My package never came, been waiting."),
            make_turn(2, "agent", "Sorry to hear that. What's your order number?"),
            make_turn(3, "customer", "Order 12345. Thanks for looking."),
        ],
    )


@pytest.fixture
def transcript_with_reference(sample_transcript):
    sample_transcript.reference = "AGENT GUIDELINES: reship after 7 days."
    sample_transcript.intent = "shipping_issue/missing"
    return sample_transcript


@pytest.fixture
def signal_result_kwargs():
    """Typed level results that satisfy the Review contract."""
    return {
        "resolution": ResolutionResult(level=Resolution.resolved, rationale="r", turns=[]),
        "correctness": CorrectnessResult(level=Correctness.unverifiable, rationale="c", turns=[]),
        "customer_effort": Level3Result(level=Level3.low, rationale="e", turns=[]),
        "interaction_quality": Level3Result(level=Level3.medium, rationale="iq", turns=[]),
    }


@pytest.fixture
def minimal_review_kwargs(signal_result_kwargs):
    """Minimum required kwargs to construct a valid Review."""
    from cqr.rubric import RUBRIC_VERSION
    return {
        "transcript_id": "t1",
        "source": "test",
        "judge": "test",
        "rubric_version": RUBRIC_VERSION,
        **signal_result_kwargs,
    }


@pytest.fixture
def tmp_store(tmp_path: Path):
    return Store(tmp_path / "reviews.json")


@pytest.fixture
def abcd_convo_dict():
    """A tiny ABCD-shaped conversation dict for loader tests."""
    return {
        "convo_id": 999,
        "scenario": {
            "flow": "product_defect",
            "subflow": "return_size",
            "personal": {"customer_name": "Test", "member_level": "bronze"},
            "order": {"order_id": "OID-1"},
            "product": {},
        },
        "original": [
            ["agent", "Hi!"],
            ["customer", "I need to return."],
            ["action", "Account pulled up."],
            ["agent", "OK, why?"],
            ["customer", "Wrong size."],
        ],
        "delexed": [],
    }


@pytest.fixture
def guidelines_dict():
    """Minimal guidelines dict covering the shapes used by GuidelineIndex."""
    return {
        "Product Defect": {
            "description": "returns and refunds",
            "subflows": {
                "Return Due to Size": {
                    "actions": [
                        {"button": "Pull up Account", "text": "Get name.", "subtext": []},
                        {"button": "Validate Purchase", "text": "Confirm order.",
                         "subtext": ["Username", "Email"]},
                    ],
                },
                "Refund Status": {"actions": [{"button": "Look up", "text": "Look up status."}]},
            },
        },
        "Account Access": {
            "description": "auth issues",
            "subflows": {
                "Reset Two-Factor Auth": {"actions": [{"button": "Send Link", "text": "Send reset link."}]},
            },
        },
    }


def mock_anthropic_client(responses: list[str]):
    """Build a stub that mimics anthropic.Anthropic().messages.create(). Each
    call pops the next string from `responses` and returns it as message text."""
    calls: list[dict] = []

    def create(**kwargs):
        calls.append(kwargs)
        text = responses.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(text=text, type="text")])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    return client, calls


def valid_review_json(**overrides) -> str:
    """A JSON string a real LLM might return — valid Review payload keyed to
    `sample_transcript` (4 turns, customer turns at idx 1 and 3)."""
    import json
    payload = {
        "risk_flags": [],
        "resolution": {"level": "resolved", "rationale": "done", "turns": [3]},
        "correctness": {"level": "supported", "rationale": "ok", "turns": []},
        "customer_effort": {"level": "low", "rationale": "one loop", "turns": []},
        "interaction_quality": {"level": "medium", "rationale": "polite", "turns": []},
        "sentiment_trajectory": [{"turn": 1, "score": -0.2}, {"turn": 3, "score": 0.5}],
        "needs_human_review": False,
        "summary": "resolved cleanly",
    }
    payload.update(overrides)
    return json.dumps(payload)
