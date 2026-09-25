"""JSON-file store. A real deployment swaps this for a table; the API doesn't care."""
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
        if self.path.exists():
            blob = json.loads(self.path.read_text())
            self._reviews = {k: Review.model_validate(v) for k, v in blob.get("reviews", {}).items()}
            self._transcripts = {k: Transcript.model_validate(v) for k, v in blob.get("transcripts", {}).items()}

    def put(self, t: Transcript, r: Review) -> None:
        self._transcripts[t.id] = t
        self._reviews[t.id] = r

    def get(self, id_: str) -> tuple[Transcript, Review] | None:
        if id_ in self._reviews:
            return self._transcripts[id_], self._reviews[id_]
        return None

    def all(self) -> list[Review]:
        return list(self._reviews.values())

    def flush(self) -> None:
        self.path.write_text(json.dumps({
            "reviews": {k: v.model_dump(mode="json") for k, v in self._reviews.items()},
            "transcripts": {k: v.model_dump(mode="json") for k, v in self._transcripts.items()},
        }, indent=1))
