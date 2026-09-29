"""
Stale-review detection.

A stored review is stale when the world has moved on relative to when it was
written. Three ways that can happen:

- `review.schema_version != SCHEMA_VERSION`  (the CQR schema evolved)
- `review.rubric_version != RUBRIC_VERSION`  (the rubric changed — scores
  emitted against an older rubric are not comparable to newer ones)
- The transcript's intent resolves (via the current `GuidelineIndex`) to a
  reference whose hash differs from `review.reference_version`. Inline
  references (references directly on the transcript) never go stale — the
  stored transcript carries them, so what was scored is what's still there.

Staleness is computed at read time and never persisted True. `cqr rereview`
picks up stale reviews and re-runs the judge, bypassing the content cache.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from . import rubric as _rubric  # for _rubric.RUBRIC_VERSION read at call time
from . import schema as _schema  # for _schema.SCHEMA_VERSION read at call time
from .loader import GuidelineIndex
from .schema import Review, Transcript, reference_version


def is_stale(review: Review, transcript: Optional[Transcript] = None,
             guidelines: Optional[GuidelineIndex] = None) -> bool:
    if review.schema_version != _schema.SCHEMA_VERSION:
        return True
    if review.rubric_version != _rubric.RUBRIC_VERSION:
        return True
    if guidelines is not None and transcript is not None and transcript.intent:
        flow, _, subflow = transcript.intent.partition("/")
        current_ref = guidelines.reference_for(flow, subflow)
        if current_ref is not None:
            if reference_version(current_ref) != review.reference_version:
                return True
    return False


def load_guidelines() -> Optional[GuidelineIndex]:
    """Lazy load `data/abcd/guidelines.json` (or `$CQR_GUIDELINES_PATH`) for
    stale checks. Returns None if the file isn't present — that's fine; the
    reference-version check is best-effort and only fires when a guideline
    dict is available."""
    path = Path(os.environ.get("CQR_GUIDELINES_PATH", "data/abcd/guidelines.json"))
    if not path.exists():
        return None
    kb_path = path.parent / "kb.json"
    return GuidelineIndex.load(path, kb_path=kb_path if kb_path.exists() else None)
