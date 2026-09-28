"""
In-process bounded batch queue backing `POST /review/batch`.

Each submit registers a `Job` (schema.py) holding N transcripts. A pool of
N worker coroutines pulls items from `asyncio.Queue` and runs the (blocking)
judge via `asyncio.to_thread`, so the loop stays free to serve `/jobs`
polling. Store mutations happen only on the loop thread — workers reach the
store between awaits.

After `CQR_CIRCUIT_THRESHOLD` consecutive retryable failures on a single
job, the remaining items short-circuit as `CircuitOpen` and the job moves
to `completed` (with the shorted items recorded as errors). Config-scope
failures — meaning `JobError.retryable=False` from a `JudgeError` whose
scope is 'config' — flip the job to `failed` and stop further work; Part 1
never emits those (every raised exception is treated as retryable
transcript-scope), Part 2 adds the classification.

Idempotency: a submit with an `idempotency_key` already seen returns the
existing job without re-enqueueing. Completed/failed jobs beyond
`max_jobs_kept` are evicted LRU; running/queued jobs are never evicted.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Callable, Optional

from .schema import Job, JobError, JobStatus, Review, Transcript
from .store import Store


class JobRunner:
    """One instance per app. `await start()` before serving traffic,
    `await stop()` on shutdown. `submit()` returns immediately with a Job
    in `queued`; workers move it through `running` → `completed`."""

    def __init__(self, judge_factory: Callable, store: Store,
                 concurrency: int = 4, max_jobs_kept: int = 100,
                 circuit_threshold: int = 5):
        self._judge_factory = judge_factory
        self._store = store
        self._concurrency = max(1, concurrency)
        self._max_jobs_kept = max_jobs_kept
        self._circuit_threshold = circuit_threshold
        self._queue: asyncio.Queue[tuple[str, Transcript]] = asyncio.Queue()
        self._jobs: OrderedDict[str, Job] = OrderedDict()
        self._idempotency: dict[str, str] = {}
        self._consec_retryable: dict[str, int] = {}   # job_id -> current streak
        self._circuit_open_jobs: set[str] = set()     # job_id -> latched open
        self._workers: list[asyncio.Task] = []
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._workers = [asyncio.create_task(self._worker(i))
                         for i in range(self._concurrency)]

    async def stop(self) -> None:
        self._running = False
        for w in self._workers:
            w.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    def submit(self, transcripts: list[Transcript],
               idempotency_key: Optional[str] = None) -> Job:
        if idempotency_key and idempotency_key in self._idempotency:
            existing = self._jobs.get(self._idempotency[idempotency_key])
            if existing is not None:
                self._jobs.move_to_end(existing.id)
                return existing

        job_id = str(uuid.uuid4())
        job = Job(id=job_id, total=len(transcripts),
                  idempotency_key=idempotency_key)
        self._jobs[job_id] = job
        if idempotency_key:
            self._idempotency[idempotency_key] = job_id
        for t in transcripts:
            self._queue.put_nowait((job_id, t))
        # A newly-submitted empty batch has no work — mark it done immediately.
        if job.total == 0:
            job.status = JobStatus.completed
            job.completed_at = datetime.now(UTC)
        self._evict_if_needed()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[Job]:
        return list(self._jobs.values())

    def reviews_for(self, job_id: str) -> list[Review]:
        return [r for r in self._store.all() if r.job_id == job_id]

    async def _worker(self, wid: int) -> None:
        try:
            judge = self._judge_factory()
        except Exception:  # noqa: BLE001
            # Failed to construct a judge (e.g. no API key). Workers can't run.
            return
        try:
            while self._running:
                job_id, t = await self._queue.get()
                try:
                    await self._process_one(judge, job_id, t)
                finally:
                    self._queue.task_done()
        except asyncio.CancelledError:
            return

    async def _process_one(self, judge, job_id: str, t: Transcript) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return  # evicted mid-flight; nothing to update
        if job.status == JobStatus.queued:
            job.status = JobStatus.running
        if job.status == JobStatus.failed:
            self._bump_completed(job)
            return
        if job_id in self._circuit_open_jobs:
            job.errors.append(JobError(
                transcript_id=t.id, error_type="CircuitOpen",
                message=f"circuit opened after {self._circuit_threshold} consecutive retryable failures",
                retryable=False, attempts=0,
            ))
            self._bump_completed(job)
            return
        try:
            review = await asyncio.to_thread(judge.judge, t)
            review.job_id = job_id
            self._store.put(t, review)
            self._consec_retryable[job_id] = 0  # success resets the streak
        except Exception as e:  # noqa: BLE001
            job.errors.append(JobError(
                transcript_id=t.id, error_type=type(e).__name__,
                message=str(e)[:400], retryable=True, attempts=1,
            ))
            streak = self._consec_retryable.get(job_id, 0) + 1
            self._consec_retryable[job_id] = streak
            if streak >= self._circuit_threshold:
                self._circuit_open_jobs.add(job_id)
        finally:
            self._bump_completed(job)

    def _bump_completed(self, job: Job) -> None:
        job.completed += 1
        if job.completed % 5 == 0 and job.completed < job.total:
            self._store.flush()
        if job.completed >= job.total:
            self._store.flush()
            if job.status != JobStatus.failed:
                job.status = JobStatus.completed
                job.completed_at = datetime.now(UTC)

    def _evict_if_needed(self) -> None:
        while len(self._jobs) > self._max_jobs_kept:
            for jid, j in list(self._jobs.items()):
                if j.status in (JobStatus.completed, JobStatus.failed):
                    if j.idempotency_key:
                        self._idempotency.pop(j.idempotency_key, None)
                    self._consec_retryable.pop(jid, None)
                    self._circuit_open_jobs.discard(jid)
                    del self._jobs[jid]
                    break
            else:
                return  # nothing evictable right now


async def wait_for_job(runner: JobRunner, job_id: str, timeout_s: float,
                       poll_s: float = 0.05) -> Optional[Job]:
    """Block up to `timeout_s` waiting for `job_id` to reach a terminal state.
    Returns whatever job state is current at return (may still be running)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job is None:
            return None
        if job.status in (JobStatus.completed, JobStatus.failed):
            return job
        await asyncio.sleep(poll_s)
    return runner.get(job_id)
