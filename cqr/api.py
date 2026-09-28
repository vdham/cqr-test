"""
HTTP contract.

  POST /review                   Transcript                        -> 200 Review
  POST /review/batch             {transcripts: [...]}              -> 202 BatchAccepted (or 200 Job with ?wait=true)
  GET  /jobs                                                       -> 200 [Job]
  GET  /jobs/{id}                                                  -> 200 Job | 404
  GET  /jobs/{id}/reviews                                          -> 200 [Review] riskiest first
  GET  /reviews  ?needs_human_review=&source=&job_id=              -> 200 [Review]
  GET  /reviews/{id}                                               -> 200 {transcript, review} | 404
  GET  /health                                                     -> 200
  GET  /                                                           -> dashboard

Every non-2xx response — including 404 and 429 — serializes to `ErrorBody`
(schema.py) except FastAPI's own 422 body-validation error, which keeps its
native `{"detail": [...]}` shape. See the README's Error contract section;
`tests/test_error_contract.py` pins the spec against the code.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from .errors import (JudgeError, JudgeOutputInvalid, JudgeRejected,
                     JudgeUnavailable, NotFoundError, PayloadTooLarge,
                     QueueFull, TranscriptRejected)
from .jobs import JobRunner, wait_for_job
from .judge import get_judge
from .schema import (BatchAccepted, BatchReviewRequest, ErrorBody, Job, Review,
                     Transcript, sort_key)
from .store import Store


_MAX_BODY_BYTES = int(os.environ.get("CQR_MAX_BODY_BYTES", "5000000"))
_RETRY_AFTER_S = 30

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
        max_queue_size=int(os.environ.get("CQR_MAX_QUEUE_SIZE", "0")),
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


# ------------------------------------------------------ error-contract matrix ----
# Single source of truth for what statuses each route can return and what
# body shape they carry. Reused in @app decorator responses={...} AND in
# tests/test_error_contract.py (parity check) AND in the README (rendered by
# scripts/render_error_table.py). Adding a status here without wiring the code
# to emit it, or vice-versa, breaks the tests.


def _err_response(error_type: str, description: str, header_retry_after: bool = False) -> dict:
    """Build one responses={} entry for an ErrorBody-shaped error."""
    entry: dict = {"model": ErrorBody, "description": description}
    if header_retry_after:
        entry["headers"] = {"Retry-After": {
            "schema": {"type": "integer"},
            "description": "Seconds to wait before retrying.",
        }}
    return entry


_ERR_413 = _err_response("PayloadTooLarge",
    "PayloadTooLarge — request body exceeded CQR_MAX_BODY_BYTES.")
_ERR_422_MIXED = {
    "description": ("Request body failed validation (HTTPValidationError, FastAPI's "
                    "native `{detail: [...]}` shape) — OR TranscriptRejected: the "
                    "provider accepted the request but rejected this transcript "
                    "(too long, content policy). Callers must branch on body shape."),
    "content": {"application/json": {"schema": {"oneOf": [
        {"$ref": "#/components/schemas/HTTPValidationError"},
        {"$ref": "#/components/schemas/ErrorBody"},
    ]}}},
}
_ERR_429 = _err_response("QueueFull",
    "QueueFull — in-process job queue at capacity. Retryable; back off and resubmit.",
    header_retry_after=True)
_ERR_500 = _err_response("JudgeOutputInvalid",
    "JudgeOutputInvalid — provider replied but JSON never validated after retries.")
_ERR_502 = _err_response("JudgeRejected",
    "JudgeRejected — provider refused authoritatively (bad key, wrong model, permission denied). Terminal, config-scope.")
_ERR_503 = _err_response("JudgeUnavailable",
    "JudgeUnavailable — transient upstream failure. Retryable.",
    header_retry_after=True)
_ERR_404 = _err_response("NotFound",
    "NotFound — no resource with that id (may have been LRU-evicted from the job table).")


ERROR_RESPONSES: dict[str, dict[int, dict]] = {
    "review_one":    {413: _ERR_413, 422: _ERR_422_MIXED, 500: _ERR_500, 502: _ERR_502, 503: _ERR_503},
    "review_batch":  {413: _ERR_413, 422: _ERR_422_MIXED, 429: _ERR_429},
    "list_jobs":     {},
    "get_job":       {404: _ERR_404},
    "get_job_reviews": {404: _ERR_404},
    "list_reviews":  {},
    "get_review":    {404: _ERR_404},
    "health":        {},
}


# ---------------------------------------------------------- app + middleware ----

_APP_DESCRIPTION = """
Conversation Quality Reviewer — scores customer/agent transcripts on five
anchored signals and exposes them as JSON.

Every non-2xx response serializes to a uniform `ErrorBody` shape
(`error_type`, `message`, `retryable`, `attempts`, `retry_after_s`), except
FastAPI's own 422 body-validation error which keeps its native
`{detail: [...]}` shape. See the README's **Error contract** section for the
full status × error_type × client action matrix.
"""

_OPENAPI_TAGS = [
    {"name": "review", "description": "Score transcripts one at a time or in batches."},
    {"name": "jobs", "description": "Async batch queue: submit, poll, collect results."},
    {"name": "reviews", "description": "Persisted reviews across all runs."},
    {"name": "meta", "description": "Health and dashboard."},
]


app = FastAPI(
    title="Conversation Quality Reviewer",
    version="0.3",
    description=_APP_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=_lifespan,
)


@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    """Reject oversize requests before body parsing. Uses Content-Length; a
    missing header (chunked encoding) is allowed through — the endpoint's own
    validation is the second line of defense."""
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            n = int(cl)
        except ValueError:
            n = 0
        if n > _MAX_BODY_BYTES:
            body = ErrorBody(
                error_type="PayloadTooLarge",
                message=f"request body {n} bytes exceeds CQR_MAX_BODY_BYTES={_MAX_BODY_BYTES}",
                retryable=False,
            )
            return JSONResponse(status_code=413, content=body.model_dump())
    return await call_next(request)


# --------------------------------------------------------- exception handlers ----

def _body(error_type: str, message: str, retryable: bool,
          attempts: int | None = None, retry_after: int | None = None) -> dict:
    return ErrorBody(
        error_type=error_type, message=message, retryable=retryable,
        attempts=attempts, retry_after_s=retry_after,
    ).model_dump()


@app.exception_handler(JudgeError)
async def _judge_error_handler(request: Request, exc: JudgeError):
    headers = {"Retry-After": str(_RETRY_AFTER_S)} if exc.retryable else {}
    retry_after = _RETRY_AFTER_S if exc.retryable else None
    return JSONResponse(
        status_code=exc.http_status,
        content=_body(type(exc).__name__, str(exc), exc.retryable,
                      attempts=exc.attempts, retry_after=retry_after),
        headers=headers,
    )


@app.exception_handler(QueueFull)
async def _queue_full_handler(request: Request, exc: QueueFull):
    return JSONResponse(
        status_code=exc.http_status,
        content=_body("QueueFull", str(exc), retryable=True, retry_after=_RETRY_AFTER_S),
        headers={"Retry-After": str(_RETRY_AFTER_S)},
    )


@app.exception_handler(NotFoundError)
async def _not_found_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=exc.http_status,
        content=_body("NotFound", str(exc), retryable=False),
    )


# Note: PayloadTooLarge is only ever emitted directly by _limit_body_size
# above (which returns a JSONResponse inline). No route raises it, so no
# `@exception_handler(PayloadTooLarge)` is needed.


# ---------------------------------------------------------------- transcripts ----

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


# ------------------------------------------------------------------- routes ----

@app.post("/review", response_model=Review, tags=["review"],
          summary="Review a single conversation",
          responses=ERROR_RESPONSES["review_one"])
def review_one(t: Transcript = Body(openapi_examples=_TRANSCRIPT_EXAMPLES)):
    """Judge one transcript against the rubric. Returns a `Review` with per-signal
    levels, rationales, and turn citations. Blocks until the judge returns
    (typically 5-15s for the LLM judge, <100ms for the heuristic judge)."""
    r = judge().judge(t)
    _store.put(t, r)
    _store.flush()
    return r


@app.post("/review/batch", tags=["review"],
          summary="Enqueue a batch of transcripts for async review",
          responses={
              200: {"model": Job, "description": "?wait=true and the job reached a terminal state within CQR_BATCH_WAIT_S."},
              202: {"model": BatchAccepted, "description": "Job accepted and enqueued. Poll GET /jobs/{id}."},
              **ERROR_RESPONSES["review_batch"],
          })
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
    """Accept a batch. 202 immediately with `{job_id, status, total}`; poll
    `/jobs/{id}` or hit this endpoint with `?wait=true` to block up to
    `CQR_BATCH_WAIT_S`. Duplicate `Idempotency-Key` returns the existing job.
    Oversize batches (> `CQR_MAX_BATCH`) return 422; a full queue returns 429
    with `Retry-After`."""
    if _runner is None:
        raise QueueFull("job runner not started")
    job = _runner.submit(list(req.transcripts), idempotency_key=idempotency_key)
    if wait:
        wait_s = float(os.environ.get("CQR_BATCH_WAIT_S", "60"))
        job = await wait_for_job(_runner, job.id, wait_s) or job
        return Response(content=job.model_dump_json(), status_code=200, media_type="application/json")
    accepted = BatchAccepted(job_id=job.id, status=job.status, total=job.total)
    return Response(content=accepted.model_dump_json(), status_code=202, media_type="application/json")


@app.get("/jobs", response_model=list[Job], tags=["jobs"],
         summary="List all jobs", responses=ERROR_RESPONSES["list_jobs"])
def list_jobs():
    """Every job the runner still remembers (LRU-capped by `CQR_MAX_JOBS_KEPT`).
    Most recently created last."""
    if _runner is None:
        return []
    return _runner.list_jobs()


@app.get("/jobs/{id_}", response_model=Job, tags=["jobs"],
         summary="Get one job", responses=ERROR_RESPONSES["get_job"])
def get_job(id_: str):
    if _runner is None:
        raise NotFoundError(f"no job {id_}")
    job = _runner.get(id_)
    if job is None:
        raise NotFoundError(f"no job {id_}")
    return job


@app.get("/jobs/{id_}/reviews", response_model=list[Review], tags=["jobs"],
         summary="Reviews produced by a job",
         responses=ERROR_RESPONSES["get_job_reviews"])
def get_job_reviews(id_: str):
    """Reviews whose `job_id` matches, sorted riskiest first."""
    if _runner is None:
        raise NotFoundError(f"no job {id_}")
    job = _runner.get(id_)
    if job is None:
        raise NotFoundError(f"no job {id_}")
    return sorted(_runner.reviews_for(id_), key=sort_key)


@app.get("/reviews", response_model=list[Review], tags=["reviews"],
         summary="List stored reviews, riskiest first",
         responses=ERROR_RESPONSES["list_reviews"])
def list_reviews(
    needs_human_review: bool | None = Query(default=None),
    source: str | None = None,
    job_id: str | None = Query(default=None, description="Filter to reviews produced by one batch job."),
):
    """Return persisted reviews, sorted by risk descending."""
    rs = _store.all()
    if needs_human_review is not None:
        rs = [r for r in rs if r.needs_human_review == needs_human_review]
    if source:
        rs = [r for r in rs if r.source == source]
    if job_id:
        rs = [r for r in rs if r.job_id == job_id]
    return sorted(rs, key=sort_key)


@app.get("/reviews/{id_}", tags=["reviews"],
         summary="Get one review with its transcript",
         responses=ERROR_RESPONSES["get_review"])
def get_review(id_: str):
    """Return both the transcript and its review for a single conversation."""
    hit = _store.get(id_)
    if not hit:
        raise NotFoundError(f"no review for {id_}")
    t, r = hit
    return {"transcript": t, "review": r}


@app.get("/health", tags=["meta"], summary="Liveness check",
         responses=ERROR_RESPONSES["health"])
def health():
    """Basic liveness — no provider call. Reports which judge is configured
    and, for LLM, which model."""
    judge_env = os.environ.get("CQR_JUDGE") or ("llm" if (
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("CQR_LLM_BASE_URL")
    ) else "heuristic")
    return {
        "status": "ok",
        "judge": judge_env,
        "model": os.environ.get("CQR_MODEL", "claude-sonnet-4-5") if judge_env == "llm" else None,
        "runner_started": _runner is not None,
    }


@app.get("/", response_class=HTMLResponse, tags=["meta"],
         summary="Dashboard", include_in_schema=False)
def dashboard():
    return (Path(__file__).parent / "static" / "index.html").read_text()
