"""
Ingest transcripts from any source into the Transcript model.

Sources:
  - ABCD (asappresearch/abcd): abcd_v1.1.json.gz or abcd_sample.json + guidelines.json
  - JSONL of Transcript objects (our own format; synthetic data ships this way)
"""
from __future__ import annotations

import gzip
import json
import os
import re
import warnings
from pathlib import Path
from typing import Iterable, Optional

from .schema import Reference, Transcript, Turn, reference_version


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
        """Backward-compatible fuzzy lookup — used by `_abcd_convo_to_transcript`
        and the pre-004 tests. When flow_key + subflow_key resolve strictly,
        this returns the same text as `get(reference_id).text`; when they
        don't, `_find_subflow`'s alias + fuzzy fallback still gives a
        best-effort match. Use `get()` when you need strict id semantics."""
        hit = self._find_subflow(flow_key, subflow_key)
        if not hit:
            return None
        flow_title, sf, body = hit
        return self._build_reference(
            _FLOW_INVERSE.get(flow_title, flow_key), subflow_key, flow_title, sf, body,
        ).text

    def get(self, reference_id: str) -> Optional[Reference]:
        """Fetch a single Reference by id ('flow_key/subflow_key'). Strict:
        the flow_key must be a known ABCD flow, and (when kb.json is loaded)
        the subflow_key must be in the canonical slug set. Fuzzy fallback is
        only used for the *title-matching* step inside `_find_subflow`, not
        for tolerating a wrong flow/slug pair."""
        if "/" not in reference_id:
            return None
        flow_key, _, subflow_key = reference_id.partition("/")
        if flow_key not in self.FLOW_KEYS:
            return None
        if self.valid_slugs is not None and subflow_key not in self.valid_slugs:
            return None
        hit = self._find_subflow(flow_key, subflow_key)
        if not hit:
            return None
        flow_title, subflow_title, body = hit
        # Strict: reject fuzzy matches to a different subflow. `_find_subflow`
        # may fuzzy-match `shipping_issue/refund_status` to Shipping Status
        # inside Shipping Issue. Verify the resolved subflow title's canonical
        # slug equals the requested one.
        if _subflow_title_to_slug(subflow_title) != subflow_key:
            return None
        return self._build_reference(flow_key, subflow_key, flow_title, subflow_title, body)

    def list(self) -> list[Reference]:
        """Return every reference in the loaded guideline set (55 for the
        shipped ABCD file), sorted by reference_id.

        When kb.json is loaded, iterate the canonical 55 slugs and forward-
        resolve each. This guarantees `list()` returns exactly the slugs the
        rest of the system recognises. Without kb.json, fall back to
        iterating guideline titles and slug-ifying them (less precise for
        aliases like 'return_size' vs 'return_due_to_size')."""
        refs: list[Reference] = []
        if self.valid_slugs is not None:
            for slug in sorted(self.valid_slugs):
                hit = self._find_subflow("", slug)
                if hit is None:
                    continue
                flow_title, subflow_title, body = hit
                flow_key = _FLOW_INVERSE.get(flow_title)
                if flow_key is None:
                    continue
                refs.append(self._build_reference(flow_key, slug, flow_title, subflow_title, body))
            return sorted(refs, key=lambda r: r.reference_id)
        for flow_title, body in self.g.items():
            flow_key = _FLOW_INVERSE.get(flow_title)
            if flow_key is None:
                continue
            for subflow_title, subflow_body in body.get("subflows", {}).items():
                subflow_key = _subflow_title_to_slug(subflow_title)
                refs.append(self._build_reference(flow_key, subflow_key,
                                                   flow_title, subflow_title, subflow_body))
        return sorted(refs, key=lambda r: r.reference_id)

    def _build_reference(self, flow_key: str, subflow_key: str,
                          flow_title: str, subflow_title: str, body: dict) -> Reference:
        # Build the same text `reference_for` used to build, but directly from
        # the body dict (no re-search). Keeps `Reference.text` identical to
        # what the judge sees; `Reference.version` therefore matches
        # `Review.reference_version` for reviews scored against this ref.
        desc = self.g.get(flow_title, {}).get("description", "")
        lines = [f"FLOW: {flow_title} ({desc})", f"SUBFLOW: {subflow_title}",
                 "AGENT GUIDELINES:"]
        policy_lines: list[str] = []
        procedure_lines: list[str] = []
        for i, a in enumerate(body.get("actions", []) or [], 1):
            atype = a.get("type", "")
            button = a.get("button", "")
            text = a.get("text", "")
            lines.append(f"  {i}. [{button}] {text}")
            for st in a.get("subtext", []) or []:
                lines.append(f"       - {st}")
            line = f"[{button}] {text}"
            if atype in ("communication", "faq/policy"):
                policy_lines.append(line)
            elif atype in ("interaction", "kb query"):
                procedure_lines.append(line)
        text_blob = "\n".join(lines)
        return Reference(
            reference_id=f"{flow_key}/{subflow_key}",
            flow=flow_title,
            subflow=subflow_title,
            version=reference_version(text_blob),
            text=text_blob,
            policy_lines=policy_lines,
            procedure_lines=procedure_lines,
            source="abcd-guidelines",
        )


# Reverse maps for enumerating references from guideline titles. Populated
# once at import; if GuidelineIndex.FLOW_KEYS / ALIASES change, these must
# be regenerated (they don't in practice — they're the same data files).
_FLOW_INVERSE: dict[str, str] = {v: k for k, v in GuidelineIndex.FLOW_KEYS.items()}
_ALIAS_INVERSE: dict[str, str] = {v: k for k, v in GuidelineIndex.ALIASES.items()}


def _subflow_title_to_slug(title: str) -> str:
    """Reverse of `_snake_to_title` with ALIAS exceptions. Some subflow titles
    don't round-trip through snake_case ('Return Due to Size' <-> 'return_size',
    'Boots FAQ' <-> 'boots'); ALIASES handles those. Everything else lowercases
    and turns spaces/hyphens into underscores."""
    if title in _ALIAS_INVERSE:
        return _ALIAS_INVERSE[title]
    s = title.lower().replace("-", "_").replace(" ", "_")
    return re.sub(r"[^a-z0-9_]", "", s)


# ------------------------------------------------- module-level index ----

_INDEX: Optional[GuidelineIndex] = None


def get_index() -> Optional[GuidelineIndex]:
    """Load `data/abcd/guidelines.json` (or `$CQR_GUIDELINES`) once for the
    process. Returns None if the file isn't present — callers should treat
    that as "no reference set available" rather than an error."""
    global _INDEX
    if _INDEX is None:
        path = Path(os.environ.get("CQR_GUIDELINES", "data/abcd/guidelines.json"))
        if not path.exists():
            return None
        kb_path = path.parent / "kb.json"
        _INDEX = GuidelineIndex.load(path, kb_path=kb_path if kb_path.exists() else None)
    return _INDEX


def _reset_index_for_tests() -> None:
    """Drop the cached index so a monkeypatched `$CQR_GUIDELINES` in the next
    call gets a fresh load. Only meant for tests."""
    global _INDEX
    _INDEX = None


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
