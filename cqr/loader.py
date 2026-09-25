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
from pathlib import Path
from typing import Iterable, Optional

from .schema import Transcript, Turn


# ------------------------------------------------------------- ABCD ---------

def _norm(s: str) -> set[str]:
    s = s.lower().replace("-", " ").replace("_", " ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    stop = {"due", "to", "the", "a", "of", "status", "manage", "faq"}
    return {w for w in s.split() if w and w not in stop}


class GuidelineIndex:
    """
    Maps ABCD's (flow, subflow) scenario keys to the agent guideline text.
    ABCD subflow keys are snake_case ('return_size', 'reset_2fa'); guideline
    names are titles ('Return Due to Size', 'Reset Two-Factor Auth'). We do a
    token-overlap match within the flow, with a few manual aliases.
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
        "bad_price_competitor": "Bad Price Competitor",
        "bad_price_yesterday": "Bad Price Yesterday",
        "mistimed_billing_already_returned": "Mistimed Billing Already Returned",
        "mistimed_billing_never_bought": "Mistimed Billing Never Bought",
        "out_of_stock_general": "Out-of-Stock General",
        "out_of_stock_one_item": "Out-of-Stock One Item",
        "promo_code_invalid": "Promo Code Invalid",
        "promo_code_out_of_date": "Promo Code Out of Date",
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

    def __init__(self, guidelines: dict):
        self.g = guidelines

    @classmethod
    def load(cls, path: Path) -> "GuidelineIndex":
        return cls(json.loads(Path(path).read_text()))

    def _find_subflow(self, flow_key: str, subflow_key: str) -> Optional[tuple[str, str, dict]]:
        flow_name = self.FLOW_KEYS.get(flow_key)
        candidates = []
        if flow_name and flow_name in self.g:
            candidates = [(flow_name, sf, body) for sf, body in self.g[flow_name]["subflows"].items()]
        else:
            candidates = [(f, sf, body) for f, v in self.g.items() for sf, body in v["subflows"].items()]

        if subflow_key in self.ALIASES:
            want = self.ALIASES[subflow_key]
            for f, sf, body in candidates:
                if sf == want:
                    return f, sf, body

        target = _norm(subflow_key)
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
    gi = GuidelineIndex.load(data_dir / "guidelines.json") if (data_dir / "guidelines.json").exists() else None
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
