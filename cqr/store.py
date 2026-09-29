"""JSON-file store. A real deployment swaps this for a table; the API doesn't care.

Also maintains an in-memory content-address index — keyed by
`(transcript_digest, rubric_version, reference_version, judge)` — so
`find()` can answer "have we already scored this exact content with this
exact rubric/judge?" in O(1). The index is rebuilt on load and updated on
`put()`. Pre-versioning reviews (empty `transcript_digest`) are skipped —
they can never match a computed digest, so they never cache-hit.
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import Review, Transcript


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._reviews: dict[str, Review] = {}
        self._transcripts: dict[str, Transcript] = {}
        # Content-address index: (digest, rubric, reference, judge) -> transcript_id
        self._by_content: dict[tuple[str, str, str, str], str] = {}
        if self.path.exists():
            blob = json.loads(self.path.read_text())
            self._reviews = {k: Review.model_validate(v) for k, v in blob.get("reviews", {}).items()}
            self._transcripts = {k: Transcript.model_validate(v) for k, v in blob.get("transcripts", {}).items()}
            for tid, r in self._reviews.items():
                self._index(tid, r)

    def _index(self, transcript_id: str, review: Review) -> None:
        if not review.transcript_digest:
            return  # pre-versioning review; can't participate in the content cache
        key = (review.transcript_digest, review.rubric_version,
               review.reference_version, review.judge)
        self._by_content[key] = transcript_id

    def put(self, t: Transcript, r: Review) -> None:
        self._transcripts[t.id] = t
        self._reviews[t.id] = r
        self._index(t.id, r)

    def get(self, id_: str) -> tuple[Transcript, Review] | None:
        # Orphan defensively: a store where a review lost its transcript (or
        # vice versa) shouldn't crash callers — return None so callers can
        # decide (e.g. `rereview` counts it as a per-item failure).
        if id_ in self._reviews and id_ in self._transcripts:
            return self._transcripts[id_], self._reviews[id_]
        return None

    def find(self, transcript_digest: str, rubric_version: str,
             reference_version: str, judge: str) -> Review | None:
        """Content-address lookup. Returns the stored review verbatim (with
        `cache_hit=False`; callers wanting cache-hit semantics should
        `model_copy(update={"cache_hit": True})`). None on miss."""
        tid = self._by_content.get((transcript_digest, rubric_version,
                                    reference_version, judge))
        if tid is None:
            return None
        return self._reviews.get(tid)

    def all(self) -> list[Review]:
        return list(self._reviews.values())

    def flush(self) -> None:
        self.path.write_text(json.dumps({
            "reviews": {k: v.model_dump(mode="json") for k, v in self._reviews.items()},
            "transcripts": {k: v.model_dump(mode="json") for k, v in self._transcripts.items()},
        }, indent=1))
