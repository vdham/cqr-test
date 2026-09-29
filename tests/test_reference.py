"""Reference model + GuidelineIndex.list/get + get_index() singleton."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqr.loader import (GuidelineIndex, _reset_index_for_tests,
                        _subflow_title_to_slug, get_index)
from cqr.schema import Reference, reference_version
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
