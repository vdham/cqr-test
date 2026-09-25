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

from fastapi import FastAPI, HTTPException, Query
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


@app.post("/review", response_model=Review)
def review_one(t: Transcript):
    r = judge().judge(t)
    _store.put(t, r)
    _store.flush()
    return r


@app.post("/review/batch", response_model=BatchReviewResponse)
def review_batch(req: BatchReviewRequest):
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


@app.get("/reviews", response_model=list[Review])
def list_reviews(needs_human_review: bool | None = Query(default=None), source: str | None = None):
    rs = _store.all()
    if needs_human_review is not None:
        rs = [r for r in rs if r.needs_human_review == needs_human_review]
    if source:
        rs = [r for r in rs if r.source == source]
    return sorted(rs, key=_sort_key)


@app.get("/reviews/{id_}")
def get_review(id_: str):
    hit = _store.get(id_)
    if not hit:
        raise HTTPException(404, f"no review for {id_}")
    t, r = hit
    return {"transcript": t, "review": r}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "static" / "index.html").read_text()
