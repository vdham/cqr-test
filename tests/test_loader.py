"""Loader: GuidelineIndex, ABCD conversion, JSONL round-trip."""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from cqr.loader import (GuidelineIndex, _abcd_convo_to_transcript, _norm,
                        _snake_to_title, dump_jsonl, load_abcd, load_jsonl)
from cqr.schema import Transcript, Turn


# Ground-truth mapping from every kb.json slug to its (flow_key, expected
# guideline subflow title). This is the canonical set of 55 pairs the loader
# must resolve correctly. Derived by hand from guidelines.json's structure.
KB_SLUG_TO_FLOW_AND_SUBFLOW = {
    # Product Defect
    "refund_initiate": ("product_defect", "Initiate Refund"),
    "refund_update":   ("product_defect", "Update Refund"),
    "refund_status":   ("product_defect", "Refund Status"),
    "return_stain":    ("product_defect", "Return Due to Stain"),
    "return_color":    ("product_defect", "Return Due to Color"),
    "return_size":     ("product_defect", "Return Due to Size"),
    # Order Issue
    "status_mystery_fee":    ("order_issue", "Status Mystery Fee"),
    "status_delivery_time":  ("order_issue", "Status Delivery Time"),
    "status_payment_method": ("order_issue", "Status Payment Method"),
    "status_quantity":       ("order_issue", "Status Quantity"),
    "manage_upgrade":        ("order_issue", "Manage Upgrade"),
    "manage_downgrade":      ("order_issue", "Manage Downgrade"),
    "manage_create":         ("order_issue", "Manage Create"),
    "manage_cancel":         ("order_issue", "Manage Cancel"),
    # Account Access
    "recover_username": ("account_access", "Recover Username"),
    "recover_password": ("account_access", "Recover Password"),
    "reset_2fa":        ("account_access", "Reset Two-Factor Auth"),
    # Troubleshoot Site
    "credit_card":    ("troubleshoot_site", "Invalid Credit Card"),
    "shopping_cart":  ("troubleshoot_site", "Cart Not Updating"),
    "search_results": ("troubleshoot_site", "Search Not Working"),
    "slow_speed":     ("troubleshoot_site", "Website Too Slow"),
    # Manage Account
    "status_service_added":    ("manage_account", "Status Service Added"),
    "status_service_removed":  ("manage_account", "Status Service Removed"),
    "status_shipping_question": ("manage_account", "Status Shipping Question"),
    "status_credit_missing":   ("manage_account", "Status Credit Missing"),
    "manage_change_address":   ("manage_account", "Manage Change Address"),
    "manage_change_name":      ("manage_account", "Manage Change Name"),
    "manage_change_phone":     ("manage_account", "Manage Change Phone"),
    "manage_payment_method":   ("manage_account", "Manage Payment Method"),
    # Purchase Dispute
    "bad_price_competitor":            ("purchase_dispute", "Bad Price Competitor"),
    "bad_price_yesterday":             ("purchase_dispute", "Bad Price Yesterday"),
    "out_of_stock_general":            ("purchase_dispute", "Out-of-Stock General"),
    "out_of_stock_one_item":           ("purchase_dispute", "Out-of-Stock One Item"),
    "promo_code_invalid":              ("purchase_dispute", "Promo Code Invalid"),
    "promo_code_out_of_date":          ("purchase_dispute", "Promo Code Out of Date"),
    "mistimed_billing_already_returned": ("purchase_dispute", "Mistimed Billing Already Returned"),
    "mistimed_billing_never_bought":     ("purchase_dispute", "Mistimed Billing Never Bought"),
    # Shipping Issue
    "status":  ("shipping_issue", "Shipping Status"),
    "manage":  ("shipping_issue", "Manage Shipping"),
    "missing": ("shipping_issue", "Missing Item"),
    "cost":    ("shipping_issue", "Shipping Cost"),
    # Subscription Inquiry
    "status_active":       ("subscription_inquiry", "Status Active"),
    "status_due_amount":   ("subscription_inquiry", "Status Due Amount"),
    "status_due_date":     ("subscription_inquiry", "Status Due Date"),
    "manage_pay_bill":     ("subscription_inquiry", "Manage Pay Bill"),
    "manage_extension":    ("subscription_inquiry", "Manage Extension"),
    "manage_dispute_bill": ("subscription_inquiry", "Manage Dispute Bill"),
    # Single-Item Query
    "boots":  ("single_item_query", "Boots FAQ"),
    "shirt":  ("single_item_query", "Shirt FAQ"),
    "jeans":  ("single_item_query", "Jeans FAQ"),
    "jacket": ("single_item_query", "Jacket FAQ"),
    # Storewide Query
    "pricing":    ("storewide_query", "Pricing FAQ"),
    "membership": ("storewide_query", "Membership FAQ"),
    "timing":     ("storewide_query", "Timing FAQ"),
    "policy":     ("storewide_query", "Policy FAQ"),
}


def _real_gi():
    return GuidelineIndex(json.loads(Path("data/abcd/guidelines.json").read_text()))


# --------------------------------------------------------------------- _norm ----

class TestNorm:
    def test_lowercases_and_splits(self):
        assert _norm("Reset Two-Factor Auth") == {"reset", "two", "factor", "auth"}

    def test_drops_stopwords(self):
        assert _norm("Return Due to Size") == {"return", "size"}

    def test_strips_punctuation(self):
        assert _norm("out-of-stock (general)") == {"out", "stock", "general"}

    def test_status_is_not_stripped(self):
        # `status` used to be a stopword; keeping it is what fixes refund_status.
        assert "status" in _norm("Refund Status")
        assert "status" in _norm("refund_status")

    def test_manage_is_not_stripped(self):
        assert "manage" in _norm("Manage Change Address")


class TestSnakeToTitle:
    def test_basic(self):
        assert _snake_to_title("refund_status") == "Refund Status"

    def test_single_word(self):
        assert _snake_to_title("manage") == "Manage"

    def test_all_capitalized(self):
        assert _snake_to_title("manage_change_address") == "Manage Change Address"

    def test_handles_empty(self):
        assert _snake_to_title("") == ""


# ------------------------------------------------------------- GuidelineIndex ----

class TestGuidelineIndex:
    def test_exact_match_wins(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        # `refund_status` -> Title Case `Refund Status` matches directly, no alias needed.
        text = gi.reference_for("product_defect", "refund_status")
        assert text is not None
        assert "SUBFLOW: Refund Status" in text

    def test_alias_hit_when_exact_misses(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        # "return_size" Title Case would be "Return Size", not in guidelines; alias -> "Return Due to Size".
        text = gi.reference_for("product_defect", "return_size")
        assert text is not None
        assert "SUBFLOW: Return Due to Size" in text

    def test_flow_key_mapping(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        text = gi.reference_for("account_access", "reset_2fa")
        assert text is not None
        assert "FLOW: Account Access" in text
        assert "SUBFLOW: Reset Two-Factor Auth" in text

    def test_unknown_flow_falls_back_to_all_subflows(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        text = gi.reference_for("mystery_flow", "refund_status")
        assert text is not None
        assert "Refund Status" in text

    def test_empty_subflow_returns_none(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        assert gi.reference_for("mystery_flow", "") is None

    def test_reference_text_includes_actions_and_subtext(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        text = gi.reference_for("product_defect", "return_size")
        assert "[Pull up Account] Get name." in text
        assert "[Validate Purchase] Confirm order." in text
        assert "- Username" in text
        assert "- Email" in text

    def test_load_from_path(self, tmp_path, guidelines_dict):
        p = tmp_path / "g.json"
        p.write_text(json.dumps(guidelines_dict))
        gi = GuidelineIndex.load(p)
        assert gi.reference_for("product_defect", "return_size") is not None

    def test_load_with_kb_path(self, tmp_path, guidelines_dict):
        gp = tmp_path / "g.json"
        kp = tmp_path / "kb.json"
        gp.write_text(json.dumps(guidelines_dict))
        kp.write_text(json.dumps({"refund_status": ["a", "b"]}))
        gi = GuidelineIndex.load(gp, kb_path=kp)
        assert gi.valid_slugs == {"refund_status"}

    def test_load_with_missing_kb_path_is_ok(self, tmp_path, guidelines_dict):
        gp = tmp_path / "g.json"
        gp.write_text(json.dumps(guidelines_dict))
        gi = GuidelineIndex.load(gp, kb_path=tmp_path / "missing.json")
        assert gi.valid_slugs is None

    def test_fuzzy_fallback_when_no_exact_or_alias(self, guidelines_dict):
        # `refund` alone: no title-case match in guidelines, not in ALIASES.
        # Falls through to fuzzy overlap, which picks some Refund subflow.
        gi = GuidelineIndex(guidelines_dict)
        hit = gi._find_subflow("product_defect", "refund")
        assert hit is not None
        assert "Refund" in hit[1]


class TestSlugValidation:
    def test_unknown_slug_emits_warning(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict, valid_slugs={"return_size"})
        with pytest.warns(UserWarning, match="unknown ABCD subflow slug"):
            gi.reference_for("product_defect", "not_a_real_slug")

    def test_known_slug_no_warning(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict, valid_slugs={"return_size"})
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            gi.reference_for("product_defect", "return_size")

    def test_no_valid_slugs_no_warning(self, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict, valid_slugs=None)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            gi.reference_for("product_defect", "anything_at_all")


@pytest.mark.parametrize("slug,expected", [
    (slug, (flow, sub)) for slug, (flow, sub) in KB_SLUG_TO_FLOW_AND_SUBFLOW.items()
])
class TestRealAbcdSlugResolution:
    """Every kb.json slug must resolve to the correct guideline subflow. This
    is a regression on the whole guideline-lookup layer; adding a new alias or
    changing _norm without adjusting for these pairs will fail here."""

    def test_resolves_to_expected_subflow(self, slug, expected):
        flow_key, expected_subflow = expected
        hit = _real_gi()._find_subflow(flow_key, slug)
        assert hit is not None, f"{slug} resolved to None"
        assert hit[1] == expected_subflow, (
            f"{slug} resolved to {hit[1]!r}, expected {expected_subflow!r}"
        )


# ---------------------------------------------------- _abcd_convo_to_transcript ----

class TestAbcdConvoToTranscript:
    def test_id_and_source(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        assert t.id == "abcd-999"
        assert t.source == "abcd"

    def test_action_speaker_becomes_system(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        system_turns = [x for x in t.turns if x.speaker == "system"]
        assert len(system_turns) == 1
        assert system_turns[0].text == "Account pulled up."

    def test_turn_indices_are_zero_based(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        assert [x.idx for x in t.turns] == [0, 1, 2, 3, 4]

    def test_intent_built_from_flow_subflow(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        assert t.intent == "product_defect/return_size"

    def test_metadata_captures_member_level(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        assert t.metadata["flow"] == "product_defect"
        assert t.metadata["subflow"] == "return_size"
        assert t.metadata["member_level"] == "bronze"

    def test_reference_populated_when_gi_present(self, abcd_convo_dict, guidelines_dict):
        gi = GuidelineIndex(guidelines_dict)
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=gi)
        assert t.reference is not None
        assert "Return Due to Size" in t.reference

    def test_reference_none_when_gi_absent(self, abcd_convo_dict):
        t = _abcd_convo_to_transcript(abcd_convo_dict, gi=None)
        assert t.reference is None


# ------------------------------------------------------------------- load_abcd ----

class TestLoadAbcd:
    def test_reads_sample_json(self, tmp_path, abcd_convo_dict, guidelines_dict):
        (tmp_path / "guidelines.json").write_text(json.dumps(guidelines_dict))
        (tmp_path / "abcd_sample.json").write_text(json.dumps([abcd_convo_dict]))
        out = load_abcd(tmp_path, limit=10)
        assert len(out) == 1
        assert out[0].id == "abcd-999"
        assert out[0].reference is not None

    def test_limit_and_offset(self, tmp_path, abcd_convo_dict):
        convos = []
        for i in range(5):
            c = dict(abcd_convo_dict)
            c["convo_id"] = 1000 + i
            convos.append(c)
        (tmp_path / "abcd_sample.json").write_text(json.dumps(convos))
        out = load_abcd(tmp_path, limit=2, offset=2)
        assert [t.id for t in out] == ["abcd-1002", "abcd-1003"]

    def test_missing_data_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_abcd(tmp_path)

    def test_gzip_full_dataset(self, tmp_path, abcd_convo_dict):
        import gzip
        splits = {"train": [], "dev": [abcd_convo_dict], "test": []}
        gz = tmp_path / "abcd_v1.1.json.gz"
        with gzip.open(gz, "wt") as f:
            json.dump(splits, f)
        out = load_abcd(tmp_path, split="dev", limit=5)
        assert len(out) == 1
        assert out[0].id == "abcd-999"


# ---------------------------------------------------------- JSONL round-trip ----

class TestJsonlRoundTrip:
    def test_dump_then_load(self, tmp_path, sample_transcript, make_turn):
        second = Transcript(id="conv-2", source="test",
                            turns=[make_turn(0, "agent", "Hey")])
        path = tmp_path / "out.jsonl"
        dump_jsonl([sample_transcript, second], path)
        restored = load_jsonl(path)
        assert len(restored) == 2
        assert restored[0].id == sample_transcript.id
        assert restored[1].id == "conv-2"

    def test_creates_parent_dir_on_dump(self, tmp_path, sample_transcript):
        path = tmp_path / "new" / "dir" / "out.jsonl"
        dump_jsonl([sample_transcript], path)
        assert path.exists()

    def test_load_ignores_blank_lines(self, tmp_path, sample_transcript):
        path = tmp_path / "out.jsonl"
        path.write_text(sample_transcript.model_dump_json() + "\n\n\n")
        assert len(load_jsonl(path)) == 1
