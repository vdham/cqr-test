"""
HTTP contract.

  POST /review               body: Transcript                   -> Review          (sync, one conversation)
  POST /review/batch         body: {transcripts: [...]}         -> 202 BatchAccepted (or 200 Job with ?wait=true)
  GET  /jobs                                                    -> [Job]
  GET  /jobs/{id}                                               -> Job
  GET  /jobs/{id}/reviews                                       -> [Review]       riskiest first
  GET  /reviews              ?needs_human_review=&source=&job_id=  -> [Review]  sorted riskiest first
  GET  /reviews/{id}                                            -> {transcript, review}
  GET  /                                                        -> dashboard

Run:  uvicorn cqr.api:app --reload
Env:  CQR_JUDGE=anthropic|heuristic   CQR_STORE=out/reviews.json   CQR_MODEL=...
      CQR_CONCURRENCY=4               CQR_MAX_BATCH=200            CQR_BATCH_WAIT_S=60
      CQR_CIRCUIT_THRESHOLD=5
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import HTMLResponse

from .jobs import JobRunner, wait_for_job
from .judge import get_judge
from .schema import (BatchAccepted, BatchReviewRequest, Job, Review, Transcript,
                     sort_key)
from .store import Store

_store = Store(Path(os.environ.get("CQR_STORE", "out/reviews.json")))
_judge = None
_runner: JobRunner | None = None


def judge():
    global _judge
    if _judge is None:
        _judge = get_judge()
    return _judge


def _make_runner() -> JobRunner:
    return JobRunner(
        judge_factory=get_judge,
        store=_store,
        concurrency=int(os.environ.get("CQR_CONCURRENCY", "4")),
        max_jobs_kept=int(os.environ.get("CQR_MAX_JOBS_KEPT", "100")),
        circuit_threshold=int(os.environ.get("CQR_CIRCUIT_THRESHOLD", "5")),
    )


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _runner
    _runner = _make_runner()
    await _runner.start()
    try:
        yield
    finally:
        await _runner.stop()
        _runner = None


app = FastAPI(title="Conversation Quality Reviewer", version="0.2", lifespan=_lifespan)


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


@app.post(
    "/review/batch",
    responses={
        200: {"model": Job, "description": "Returned when ?wait=true and the job reached a terminal state within CQR_BATCH_WAIT_S."},
        202: {"model": BatchAccepted, "description": "Job accepted and enqueued. Poll GET /jobs/{id}."},
    },
    summary="Enqueue a batch of transcripts for async review",
)
async def review_batch(
    req: BatchReviewRequest = Body(openapi_examples={
        "two_transcripts": {
            "summary": "Batch of two transcripts",
            "value": {"transcripts": [_TRANSCRIPT_EXAMPLES["minimal"]["value"],
                                       _TRANSCRIPT_EXAMPLES["with_reference"]["value"]]},
        },
    }),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    wait: bool = Query(default=False, description="If true, block up to CQR_BATCH_WAIT_S for the job to complete and return 200 with the Job."),
):
    """Accept a batch of transcripts. Returns 202 immediately with a `BatchAccepted`
    body carrying a `job_id` you can poll at `/jobs/{id}`. With `?wait=true`, block
    up to `CQR_BATCH_WAIT_S` seconds (default 60) and return **200** with the full
    `Job` if it reaches a terminal state in that window, otherwise 200 with the
    still-running job snapshot. Duplicate `Idempotency-Key` returns the existing
    job without re-enqueueing."""
    if _runner is None:
        raise HTTPException(503, "job runner not started")
    job = _runner.submit(list(req.transcripts), idempotency_key=idempotency_key)
    if wait:
        wait_s = float(os.environ.get("CQR_BATCH_WAIT_S", "60"))
        job = await wait_for_job(_runner, job.id, wait_s) or job
        return Response(content=job.model_dump_json(), status_code=200, media_type="application/json")
    accepted = BatchAccepted(job_id=job.id, status=job.status, total=job.total)
    return Response(content=accepted.model_dump_json(), status_code=202, media_type="application/json")


@app.get("/jobs", response_model=list[Job], summary="List all jobs")
def list_jobs():
    """Every job the runner still remembers (LRU-capped by `CQR_MAX_JOBS_KEPT`).
    Most recently created last."""
    if _runner is None:
        raise HTTPException(503, "job runner not started")
    return _runner.list_jobs()


@app.get("/jobs/{id_}", response_model=Job, summary="Get one job")
def get_job(id_: str):
    if _runner is None:
        raise HTTPException(503, "job runner not started")
    job = _runner.get(id_)
    if job is None:
        raise HTTPException(404, f"no job {id_}")
    return job


@app.get("/jobs/{id_}/reviews", response_model=list[Review], summary="Reviews produced by a job")
def get_job_reviews(id_: str):
    """The reviews whose `job_id` matches, sorted riskiest first (same ordering
    as `/reviews`)."""
    if _runner is None:
        raise HTTPException(503, "job runner not started")
    job = _runner.get(id_)
    if job is None:
        raise HTTPException(404, f"no job {id_}")
    return sorted(_runner.reviews_for(id_), key=sort_key)


@app.get("/reviews", response_model=list[Review], summary="List stored reviews, riskiest first")
def list_reviews(
    needs_human_review: bool | None = Query(default=None),
    source: str | None = None,
    job_id: str | None = Query(default=None, description="Filter to reviews produced by one batch job."),
):
    """Return persisted reviews, sorted by risk descending. Filter with
    `?needs_human_review=true`, `?source=abcd|synthetic|upload`, or
    `?job_id=...` (single value)."""
    rs = _store.all()
    if needs_human_review is not None:
        rs = [r for r in rs if r.needs_human_review == needs_human_review]
    if source:
        rs = [r for r in rs if r.source == source]
    if job_id:
        rs = [r for r in rs if r.job_id == job_id]
    return sorted(rs, key=sort_key)


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
