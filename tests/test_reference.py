"""Reference model + GuidelineIndex.list/get + get_index() singleton."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqr import api
from cqr.judge import HeuristicJudge
from cqr.loader import (GuidelineIndex, _reset_index_for_tests,
                        _subflow_title_to_slug, get_index)
from cqr.schema import Reference, reference_version
from cqr.store import Store
from tests.test_loader import KB_SLUG_TO_FLOW_AND_SUBFLOW


def _real_gi() -> GuidelineIndex:
    """Load the shipped guidelines + kb.json (kb makes `list()` iterate the
    canonical 55 slugs rather than falling back to title-derived ones)."""
    return GuidelineIndex.load(Path("data/abcd/guidelines.json"),
                                kb_path=Path("data/abcd/kb.json"))


class TestReferenceModel:
    def test_fields_have_defaults(self):
        r = Reference(reference_id="a/b", flow="A", subflow="B", version="deadbeef1234",
                      text="body")
        assert r.policy_lines == []
        assert r.procedure_lines == []
        assert r.source == "abcd-guidelines"


class TestSubflowTitleToSlug:
    def test_direct_snake_case_from_title(self):
        assert _subflow_title_to_slug("Status Mystery Fee") == "status_mystery_fee"

    def test_hyphens_become_underscores(self):
        assert _subflow_title_to_slug("Out-of-Stock General") == "out_of_stock_general"

    def test_alias_inverse_for_paraphrased_titles(self):
        # ALIASES has 'return_size' -> 'Return Due to Size'. Inverse must return the slug.
        assert _subflow_title_to_slug("Return Due to Size") == "return_size"
        assert _subflow_title_to_slug("Reset Two-Factor Auth") == "reset_2fa"
        assert _subflow_title_to_slug("Boots FAQ") == "boots"


class TestGuidelineIndexList:
    def test_returns_55_references(self):
        gi = _real_gi()
        refs = gi.list()
        assert len(refs) == 55

    def test_all_reference_ids_are_in_kb(self):
        gi = _real_gi()
        refs = gi.list()
        ids = {r.reference_id for r in refs}
        for slug, (flow_key, _) in KB_SLUG_TO_FLOW_AND_SUBFLOW.items():
            expected_id = f"{flow_key}/{slug}"
            assert expected_id in ids, f"{expected_id} missing from list()"

    def test_list_is_sorted(self):
        gi = _real_gi()
        ids = [r.reference_id for r in gi.list()]
        assert ids == sorted(ids)

    def test_each_reference_has_all_fields(self):
        gi = _real_gi()
        r = gi.list()[0]
        assert isinstance(r.reference_id, str) and "/" in r.reference_id
        assert isinstance(r.flow, str) and r.flow
        assert isinstance(r.subflow, str) and r.subflow
        assert isinstance(r.version, str) and len(r.version) == 12
        assert isinstance(r.text, str) and "FLOW:" in r.text and "SUBFLOW:" in r.text
        assert r.source == "abcd-guidelines"

    def test_policy_and_procedure_lines_populated(self):
        """`Return Due to Size` has both `interaction`-type steps (Pull up
        Account, Validate Purchase, ...) and a `communication`-type step
        (End Conversation) — one of each side must be non-empty."""
        gi = _real_gi()
        r = gi.get("product_defect/return_size")
        assert r is not None
        assert len(r.policy_lines) >= 1
        assert len(r.procedure_lines) >= 1
        assert all("[" in line for line in r.policy_lines)


class TestGuidelineIndexListFallback:
    """When kb.json isn't loaded, `list()` falls back to iterating guideline
    titles and slug-ifying them."""

    def test_list_from_titles_when_no_kb(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)  # no valid_slugs
        refs = gi.list()
        ids = {r.reference_id for r in refs}
        # The synthetic guidelines_dict has Product Defect + Account Access.
        assert "product_defect/return_size" in ids  # via ALIAS inverse
        assert "product_defect/refund_status" in ids  # via snake_case-from-title
        assert "account_access/reset_2fa" in ids  # via ALIAS inverse

    def test_list_skips_unknown_flow_titles(self):
        """A guideline dict with a flow title we don't recognise in
        FLOW_KEYS is skipped (both in the kb-slug path and the fallback)."""
        weird = {
            "Mystery Flow": {"description": "", "subflows": {
                "Some Subflow": {"actions": [{"button": "X", "text": "y"}]},
            }},
        }
        gi = GuidelineIndex(weird, valid_slugs={"some_subflow"})
        # kb-path: slug resolves via find_subflow to Mystery Flow; but flow_key
        # is None → skipped.
        assert gi.list() == []
        # Fallback path: same thing.
        gi2 = GuidelineIndex(weird)
        assert gi2.list() == []

    def test_list_skips_slugs_that_dont_resolve(self):
        """kb.json declares a slug that isn't in guidelines — list() drops it."""
        gi = GuidelineIndex.load(
            Path("data/abcd/guidelines.json"),
            kb_path=Path("data/abcd/kb.json"),
        )
        gi.valid_slugs = set(gi.valid_slugs) | {"totally_fake_slug"}
        ids = {r.reference_id for r in gi.list()}
        assert not any(rid.endswith("/totally_fake_slug") for rid in ids)


class TestGuidelineIndexGet:
    def test_known_id_returns_reference(self):
        gi = _real_gi()
        r = gi.get("shipping_issue/missing")
        assert r is not None
        assert r.reference_id == "shipping_issue/missing"
        assert r.flow == "Shipping Issue"
        assert r.subflow == "Missing Item"

    def test_unknown_id_returns_none(self):
        gi = _real_gi()
        assert gi.get("mystery/does_not_exist_ever") is None

    def test_malformed_id_returns_none(self):
        gi = _real_gi()
        assert gi.get("no-slash-here") is None

    def test_bogus_flow_returns_none(self):
        """Strict: `get` demands a known flow_key even if the subflow_key
        exists — no fuzzy fallback across all flows (that stays in `reference_for`)."""
        gi = _real_gi()
        assert gi.get("mystery_flow/missing") is None

    def test_bogus_slug_returns_none_when_kb_loaded(self):
        """With kb.json loaded, `get` demands the subflow_key be canonical."""
        gi = _real_gi()
        assert gi.get("shipping_issue/not_a_real_slug") is None

    def test_slug_not_in_kb_but_flow_valid_returns_none(self):
        gi = _real_gi()
        # `refund_status` is a Product Defect slug — using it with a different
        # flow that doesn't contain it must return None (strict flow scoping).
        assert gi.get("shipping_issue/refund_status") is None

    def test_defensive_none_when_find_subflow_fails(self, tmp_path):
        """Flow_key is known and slug passes valid_slugs but _find_subflow
        returns None (guideline dict is missing this subflow). Defensive
        branch — coverage insurance."""
        gi = GuidelineIndex(
            {"Shipping Issue": {"description": "", "subflows": {}}},
            valid_slugs={"missing"},
        )
        assert gi.get("shipping_issue/missing") is None

    def test_reference_for_delegates(self):
        gi = _real_gi()
        text = gi.reference_for("shipping_issue", "missing")
        r = gi.get("shipping_issue/missing")
        assert text is not None
        assert r.text == text

    def test_version_matches_hash_of_text(self):
        gi = _real_gi()
        r = gi.get("shipping_issue/missing")
        assert r.version == reference_version(r.text)


class TestGetIndex:
    def test_returns_index_when_file_present(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        idx = get_index()
        assert idx is not None
        assert idx.get("shipping_issue/missing") is not None

    def test_returns_none_when_file_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", str(tmp_path / "nope.json"))
        _reset_index_for_tests()
        assert get_index() is None

    def test_singleton_across_calls(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        assert get_index() is get_index()

    def test_reset_forces_reload(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        first = get_index()
        _reset_index_for_tests()
        second = get_index()
        assert first is not second  # fresh instance after reset

    def test_no_kb_file_still_works(self, tmp_path, monkeypatch, guidelines_dict):
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        # kb.json intentionally absent — get_index should still return an index.
        monkeypatch.setenv("CQR_GUIDELINES", str(p))
        _reset_index_for_tests()
        idx = get_index()
        assert idx is not None
        assert idx.valid_slugs is None


# ---------------------------------------------------------- HTTP surface ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated per-test API. Guidelines default to the shipped file so
    /guidelines returns real data; individual tests can point CQR_GUIDELINES
    elsewhere via monkeypatch."""
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setenv("CQR_JUDGE", "heuristic")
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    _reset_index_for_tests()
    with TestClient(api.app) as c:
        yield c
    _reset_index_for_tests()


class TestListGuidelinesEndpoint:
    def test_returns_55_summaries(self, client):
        r = client.get("/guidelines")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 55
        first = body[0]
        assert set(first.keys()) == {"reference_id", "flow", "subflow", "version"}
        # No `text` field — that's the whole point of the list view.

    def test_empty_when_no_index(self, tmp_path, monkeypatch, client):
        monkeypatch.setenv("CQR_GUIDELINES", str(tmp_path / "nope.json"))
        _reset_index_for_tests()
        assert client.get("/guidelines").json() == []


class TestGetGuidelineEndpoint:
    def test_returns_full_reference(self, client):
        r = client.get("/guidelines/shipping_issue/missing")
        assert r.status_code == 200
        body = r.json()
        assert body["reference_id"] == "shipping_issue/missing"
        assert body["flow"] == "Shipping Issue"
        assert body["subflow"] == "Missing Item"
        assert len(body["version"]) == 12
        assert "AGENT GUIDELINES:" in body["text"]
        assert body["source"] == "abcd-guidelines"

    def test_unknown_flow_returns_404_errorbody(self, client):
        r = client.get("/guidelines/nope/missing")
        assert r.status_code == 404
        body = r.json()
        assert body["error_type"] == "NotFound"
        assert body["retryable"] is False

    def test_unknown_slug_returns_404_errorbody(self, client):
        r = client.get("/guidelines/shipping_issue/not_a_real_slug")
        assert r.status_code == 404
        assert r.json()["error_type"] == "NotFound"

    def test_no_index_returns_404(self, tmp_path, monkeypatch, client):
        monkeypatch.setenv("CQR_GUIDELINES", str(tmp_path / "nope.json"))
        _reset_index_for_tests()
        r = client.get("/guidelines/shipping_issue/missing")
        assert r.status_code == 404


class TestResolveReference:
    """resolve_reference implements the inline > id > intent > none order."""

    def _t(self, **kwargs):
        from cqr.schema import Turn as T
        turns = kwargs.pop("turns", None) or [T(idx=0, speaker="agent", text="hi"),
                                                T(idx=1, speaker="customer", text="?")]
        base = dict(id="c", source="test", turns=turns)
        base.update(kwargs)
        from cqr.schema import Transcript
        return Transcript(**base)

    def test_inline_wins(self):
        from cqr.loader import resolve_reference
        t = self._t(reference="INLINE POLICY", reference_id="shipping_issue/missing")
        text, res, ver = resolve_reference(t)
        assert text == "INLINE POLICY"
        assert res == "inline"

    def test_id_when_no_inline(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        from cqr.loader import resolve_reference
        t = self._t(reference_id="shipping_issue/missing")
        text, res, ver = resolve_reference(t)
        assert res == "id"
        assert "SUBFLOW: Missing Item" in text
        assert len(ver) == 12

    def test_intent_when_no_id(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        from cqr.loader import resolve_reference
        t = self._t(intent="shipping_issue/missing")
        text, res, ver = resolve_reference(t)
        assert res == "intent"
        assert "SUBFLOW: Missing Item" in text

    def test_unknown_intent_falls_to_none(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        from cqr.loader import resolve_reference
        t = self._t(intent="totally/nonexistent")
        text, res, ver = resolve_reference(t)
        assert text is None
        assert res == "none"
        assert ver == "none"

    def test_none_when_nothing_supplied(self):
        from cqr.loader import resolve_reference
        t = self._t()
        text, res, ver = resolve_reference(t)
        assert text is None
        assert res == "none"

    def test_unknown_reference_id_raises_transcript_rejected(self, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        _reset_index_for_tests()
        from cqr.errors import TranscriptRejected
        from cqr.loader import resolve_reference
        t = self._t(reference_id="does_not/exist")
        with pytest.raises(TranscriptRejected):
            resolve_reference(t)

    def test_id_without_index_raises(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES", str(tmp_path / "nope.json"))
        _reset_index_for_tests()
        from cqr.errors import TranscriptRejected
        from cqr.loader import resolve_reference
        t = self._t(reference_id="shipping_issue/missing")
        with pytest.raises(TranscriptRejected):
            resolve_reference(t)


class TestReviewRecordsResolution:
    """`_finalize` stamps reference_id + reference_resolution based on how
    the reference was resolved. The API returns them on the response."""

    @pytest.fixture
    def api_client(self, tmp_path, monkeypatch):
        monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
        monkeypatch.setenv("CQR_JUDGE", "heuristic")
        monkeypatch.setenv("CQR_GUIDELINES", "data/abcd/guidelines.json")
        monkeypatch.setattr(api, "_judge", HeuristicJudge())
        _reset_index_for_tests()
        with TestClient(api.app) as c:
            yield c
        _reset_index_for_tests()

    def _body(self, id_, **kwargs):
        base = {
            "id": id_, "source": "upload",
            "turns": [{"idx": 0, "speaker": "agent", "text": "hi"},
                      {"idx": 1, "speaker": "customer", "text": "?"}],
        }
        base.update(kwargs)
        return base

    def test_id_resolution_recorded(self, api_client):
        body = self._body("id-1", reference_id="shipping_issue/missing")
        r = api_client.post("/review", json=body).json()
        assert r["reference_resolution"] == "id"
        assert r["reference_id"] == "shipping_issue/missing"
        # reference_version matches GET /guidelines/{id}.version
        expected = api_client.get("/guidelines/shipping_issue/missing").json()["version"]
        assert r["reference_version"] == expected

    def test_intent_resolution_recorded(self, api_client):
        body = self._body("intent-1", intent="shipping_issue/missing")
        r = api_client.post("/review", json=body).json()
        assert r["reference_resolution"] == "intent"
        assert r["reference_id"] == "shipping_issue/missing"

    def test_inline_resolution_recorded(self, api_client):
        body = self._body("inline-1", reference="AGENT GUIDELINES: reship after 7 days")
        r = api_client.post("/review", json=body).json()
        assert r["reference_resolution"] == "inline"
        assert r["reference_id"] is None
        assert len(r["reference_version"]) == 12

    def test_none_forces_unverifiable(self, api_client):
        body = self._body("none-1")  # no reference, no id, no intent
        r = api_client.post("/review", json=body).json()
        assert r["reference_resolution"] == "none"
        assert r["reference_id"] is None
        assert r["reference_version"] == "none"
        assert r["correctness"]["level"] == "unverifiable"

    def test_unknown_id_returns_422_transcript_rejected(self, api_client):
        body = self._body("bad-id", reference_id="does_not/exist")
        r = api_client.post("/review", json=body)
        assert r.status_code == 422
        body = r.json()
        assert body["error_type"] == "TranscriptRejected"

    def test_unknown_intent_falls_to_none(self, api_client):
        body = self._body("mystery-intent", intent="mystery/one")
        r = api_client.post("/review", json=body).json()
        assert r["reference_resolution"] == "none"
        assert r["correctness"]["level"] == "unverifiable"


class TestHealthGuidelines:
    def test_guidelines_block_shape(self, client):
        body = client.get("/health").json()
        g = body["guidelines"]
        assert g["count"] == 55
        assert g["source"] == "abcd-guidelines"
        assert isinstance(g["index_version"], str) and len(g["index_version"]) == 12

    def test_guidelines_block_when_no_index(self, tmp_path, monkeypatch, client):
        monkeypatch.setenv("CQR_GUIDELINES", str(tmp_path / "nope.json"))
        _reset_index_for_tests()
        g = client.get("/health").json()["guidelines"]
        assert g == {"count": 0, "source": None, "index_version": "none"}
