"""
Ingest transcripts from any source into the Transcript model.

Sources:
  - ABCD (asappresearch/abcd): abcd_v1.1.json.gz or abcd_sample.json + guidelines.json
  - JSONL of Transcript objects (our own format; synthetic data ships this way)
"""
from __future__ import annotations

import gzip
import json
import re
import warnings
from pathlib import Path
from typing import Iterable, Optional

from .schema import Transcript, Turn


# ------------------------------------------------------------- ABCD ---------

# `status` and `manage` used to be stop-words, which broke intents like
# `refund_status` (collapsed to {refund}, tied against `Initiate Refund` and
# `Update Refund`, first-wins returned the wrong policy). They are actually the
# discriminating tokens in Order Issue, Manage Account, and Subscription
# Inquiry, so they stay in the token set.
_STOPWORDS = {"due", "to", "the", "a", "of", "faq"}


def _norm(s: str) -> set[str]:
    s = s.lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return {w for w in s.split() if w and w not in _STOPWORDS}


def _snake_to_title(s: str) -> str:
    """`refund_status` -> `Refund Status`. Used to try an exact-name match
    before falling back to aliases or fuzzy overlap."""
    return " ".join(part.capitalize() for part in s.split("_") if part)


class GuidelineIndex:
    """
    Maps ABCD's (flow, subflow) scenario keys to the agent guideline text.
    ABCD subflow keys are snake_case ('return_size', 'reset_2fa'); guideline
    names are titles ('Return Due to Size', 'Reset Two-Factor Auth'). Resolution
    strategy is ordered:

        1. exact match — snake_case_to_title(subflow_key) equals a subflow
           name in the target flow. Handles `refund_status`, `manage_cancel`,
           `status_service_added`, and every other kb.json slug whose Title
           Case form matches the guideline verbatim.
        2. explicit alias — hand-curated mapping for the cases the dataset
           reworded ('return_size' -> 'Return Due to Size', 'reset_2fa' ->
           'Reset Two-Factor Auth', 'refund_update' -> 'Update Refund').
        3. fuzzy token overlap — last resort. `_norm` intersect count wins.
    """

    ALIASES = {
        "reset_2fa": "Reset Two-Factor Auth",
        "return_size": "Return Due to Size",
        "return_color": "Return Due to Color",
        "return_stain": "Return Due to Stain",
        "cost": "Shipping Cost",
        "missing": "Missing Item",
        "slow_speed": "Website Too Slow",
        "search_results": "Search Not Working",
        "shopping_cart": "Cart Not Updating",
        "credit_card": "Invalid Credit Card",
        "status": "Shipping Status",
        "manage": "Manage Shipping",
        "status_questions": "Status Active",
        "refund_initiate": "Initiate Refund",
        "refund_update": "Update Refund",
        "refund_status": "Refund Status",  # defensive; exact-match handles it too
        "bad_price_competitor": "Bad Price Competitor",
        "bad_price_yesterday": "Bad Price Yesterday",
        "mistimed_billing_already_returned": "Mistimed Billing Already Returned",
        "mistimed_billing_never_bought": "Mistimed Billing Never Bought",
        "out_of_stock_general": "Out-of-Stock General",
        "out_of_stock_one_item": "Out-of-Stock One Item",
        "promo_code_invalid": "Promo Code Invalid",
        "promo_code_out_of_date": "Promo Code Out of Date",
        "boots": "Boots FAQ",
        "shirt": "Shirt FAQ",
        "jeans": "Jeans FAQ",
        "jacket": "Jacket FAQ",
        "pricing": "Pricing FAQ",
        "membership": "Membership FAQ",
        "timing": "Timing FAQ",
        "policy": "Policy FAQ",
    }

    FLOW_KEYS = {
        "product_defect": "Product Defect",
        "order_issue": "Order Issue",
        "account_access": "Account Access",
        "troubleshoot_site": "Troubleshoot Site",
        "manage_account": "Manage Account",
        "purchase_dispute": "Purchase Dispute",
        "shipping_issue": "Shipping Issue",
        "subscription_inquiry": "Subscription Inquiry",
        "single_item_query": "Single-Item Query",
        "storewide_query": "Storewide Query",
    }

    def __init__(self, guidelines: dict, valid_slugs: Optional[set[str]] = None):
        self.g = guidelines
        self.valid_slugs = valid_slugs  # if provided, unknown subflow_key emits a warning

    @classmethod
    def load(cls, path: Path, kb_path: Optional[Path] = None) -> "GuidelineIndex":
        guidelines = json.loads(Path(path).read_text())
        valid_slugs: Optional[set[str]] = None
        if kb_path is not None:
            kb_path = Path(kb_path)
            if kb_path.exists():
                valid_slugs = set(json.loads(kb_path.read_text()).keys())
        return cls(guidelines, valid_slugs=valid_slugs)

    def _find_subflow(self, flow_key: str, subflow_key: str) -> Optional[tuple[str, str, dict]]:
        if self.valid_slugs is not None and subflow_key and subflow_key not in self.valid_slugs:
            warnings.warn(
                f"unknown ABCD subflow slug {subflow_key!r} (not in kb.json); "
                "falling back to fuzzy match",
                stacklevel=2,
            )

        flow_name = self.FLOW_KEYS.get(flow_key)
        if flow_name and flow_name in self.g:
            candidates = [(flow_name, sf, body) for sf, body in self.g[flow_name]["subflows"].items()]
        else:
            candidates = [(f, sf, body) for f, v in self.g.items() for sf, body in v["subflows"].items()]

        if subflow_key:
            title = _snake_to_title(subflow_key)
            for f, sf, body in candidates:
                if sf == title:
                    return f, sf, body

        if subflow_key in self.ALIASES:
            want = self.ALIASES[subflow_key]
            for f, sf, body in candidates:
                if sf == want:
                    return f, sf, body

        target = _norm(subflow_key)
        if not target:
            return None
        best, best_score = None, 0
        for f, sf, body in candidates:
            score = len(target & _norm(sf))
            if score > best_score:
                best, best_score = (f, sf, body), score
        return best

    def reference_for(self, flow_key: str, subflow_key: str) -> Optional[str]:
        hit = self._find_subflow(flow_key, subflow_key)
        if not hit:
            return None
        flow, sf, body = hit
        lines = [f"FLOW: {flow} ({self.g[flow].get('description','')})", f"SUBFLOW: {sf}", "AGENT GUIDELINES:"]
        for i, a in enumerate(body.get("actions", []), 1):
            lines.append(f"  {i}. [{a.get('button','')}] {a.get('text','')}")
            for st in a.get("subtext", []) or []:
                lines.append(f"       - {st}")
        return "\n".join(lines)


def _abcd_convo_to_transcript(c: dict, gi: Optional[GuidelineIndex]) -> Transcript:
    scen = c["scenario"]
    flow, subflow = scen.get("flow", ""), scen.get("subflow", "")
    turns = []
    for i, (spk, text) in enumerate(c["original"]):
        speaker = {"agent": "agent", "customer": "customer"}.get(spk, "system")
        turns.append(Turn(idx=i, speaker=speaker, text=text))
    return Transcript(
        id=f"abcd-{c['convo_id']}",
        source="abcd",
        turns=turns,
        intent=f"{flow}/{subflow}",
        reference=gi.reference_for(flow, subflow) if gi else None,
        metadata={"flow": flow, "subflow": subflow, "member_level": scen.get("personal", {}).get("member_level")},
    )


def load_abcd(data_dir: Path, split: str = "dev", limit: int = 20, offset: int = 0) -> list[Transcript]:
    data_dir = Path(data_dir)
    gi = None
    if (data_dir / "guidelines.json").exists():
        gi = GuidelineIndex.load(data_dir / "guidelines.json", kb_path=data_dir / "kb.json")
    full = data_dir / "abcd_v1.1.json.gz"
    sample = data_dir / "abcd_sample.json"
    if full.exists():
        with gzip.open(full, "rt") as f:
            convos = json.load(f)[split]
    elif sample.exists():
        convos = json.loads(sample.read_text())
    else:
        raise FileNotFoundError(f"No ABCD data in {data_dir}")
    return [_abcd_convo_to_transcript(c, gi) for c in convos[offset: offset + limit]]


# ------------------------------------------------------------ JSONL ---------

def load_jsonl(path: Path) -> list[Transcript]:
    out = []
    for line in Path(path).read_text().splitlines():
        if line.strip():
            out.append(Transcript.model_validate_json(line))
    return out


def dump_jsonl(transcripts: Iterable[Transcript], path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for t in transcripts:
            f.write(t.model_dump_json() + "\n")
