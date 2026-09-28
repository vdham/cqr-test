"""
HTTP contract.

  POST /review            body: Transcript            -> Review          (sync, one conversation)
  POST /review/batch      body: {transcripts: [...]}  -> {reviews, errors}
  GET  /reviews           ?needs_human_review=true   -> [Review]  sorted riskiest first
  GET  /reviews/{id}                                 -> {transcript, review}
  GET  /                                             -> dashboard

Run:  uvicorn cqr.api:app --reload
Env:  CQR_JUDGE=anthropic|heuristic   CQR_STORE=out/reviews.json   CQR_MODEL=...
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from .judge import get_judge
from .schema import BatchReviewRequest, BatchReviewResponse, Review, Transcript
from .store import Store

app = FastAPI(title="Conversation Quality Reviewer", version="0.1")
_store = Store(Path(os.environ.get("CQR_STORE", "out/reviews.json")))
_judge = None


def judge():
    global _judge
    if _judge is None:
        _judge = get_judge()
    return _judge


_TRANSCRIPT_EXAMPLES = {
    "minimal": {
        "summary": "Minimal upload (no reference)",
        "description": "Two-turn conversation with no policy reference. `correctness` will be `unverifiable`.",
        "value": {
            "id": "demo-001",
            "source": "upload",
            "turns": [
                {"idx": 0, "speaker": "agent", "text": "Hi, how can I help?"},
                {"idx": 1, "speaker": "customer", "text": "My package never arrived."},
            ],
        },
    },
    "with_reference": {
        "summary": "Upload with policy reference",
        "description": "Same conversation with an inline guideline; enables the `correctness` signal.",
        "value": {
            "id": "demo-002",
            "source": "upload",
            "intent": "shipping_issue/missing",
            "reference": "AGENT GUIDELINES: If waiting < 7 days, ask the customer to wait. If waiting >= 7 days, reship.",
            "turns": [
                {"idx": 0, "speaker": "customer", "text": "My package hasn't arrived, it's been 3 days."},
                {"idx": 1, "speaker": "agent", "text": "I'll reship right now."},
            ],
        },
    },
}


@app.post("/review", response_model=Review, summary="Review a single conversation")
def review_one(t: Transcript = Body(openapi_examples=_TRANSCRIPT_EXAMPLES)):
    """Judge one transcript against the rubric. Returns a `Review` with per-signal
    levels, rationales, and turn citations. Blocks until the judge returns
    (typically 5-15s for the Anthropic judge, <100ms for the heuristic judge).
    The review is persisted to the store and appears in `/reviews` immediately."""
    r = judge().judge(t)
    _store.put(t, r)
    _store.flush()
    return r


@app.post("/review/batch", response_model=BatchReviewResponse, summary="Review many conversations")
def review_batch(
    req: BatchReviewRequest = Body(openapi_examples={
        "two_transcripts": {
            "summary": "Batch of two transcripts",
            "value": {"transcripts": [_TRANSCRIPT_EXAMPLES["minimal"]["value"],
                                       _TRANSCRIPT_EXAMPLES["with_reference"]["value"]]},
        },
    })
):
    """Judge many transcripts in one request. Failures are collected per-transcript
    into `errors` rather than aborting the batch — the response always includes
    successful `reviews` for whatever completed."""
    out, errs = [], []
    for t in req.transcripts:
        try:
            r = judge().judge(t)
            _store.put(t, r)
            out.append(r)
        except Exception as e:  # noqa: BLE001
            errs.append({"transcript_id": t.id, "error": f"{type(e).__name__}: {e}"})
    _store.flush()
    return BatchReviewResponse(reviews=out, errors=errs)


def _sort_key(r: Review):
    return (-r.max_risk, r.correctness.level != "contradicted", r.resolution.level != "unresolved", r.transcript_id)


@app.get("/reviews", response_model=list[Review], summary="List stored reviews, riskiest first")
def list_reviews(needs_human_review: bool | None = Query(default=None), source: str | None = None):
    """Return all persisted reviews, sorted by risk descending. Filter with
    `?needs_human_review=true` to get the supervisor queue (any medium+ risk
    flag or contradicted correctness), or `?source=abcd|synthetic|upload` to
    scope to one input source."""
    rs = _store.all()
    if needs_human_review is not None:
        rs = [r for r in rs if r.needs_human_review == needs_human_review]
    if source:
        rs = [r for r in rs if r.source == source]
    return sorted(rs, key=_sort_key)


@app.get("/reviews/{id_}", summary="Get one review with its transcript")
def get_review(id_: str):
    """Return both the transcript and its review for a single conversation.
    404 if the id has never been reviewed."""
    hit = _store.get(id_)
    if not hit:
        raise HTTPException(404, f"no review for {id_}")
    t, r = hit
    return {"transcript": t, "review": r}


@app.get("/", response_class=HTMLResponse, summary="Dashboard", include_in_schema=False)
def dashboard():
    return (Path(__file__).parent / "static" / "index.html").read_text()
