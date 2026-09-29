"""JobRunner: bounded queue, workers, idempotency, eviction, circuit breaker."""
from __future__ import annotations

import asyncio
import time

import pytest

from cqr.jobs import JobRunner, wait_for_job
from cqr.judge import HeuristicJudge
from cqr.schema import JobStatus, Transcript, Turn
from cqr.store import Store


def _t(id_: str) -> Transcript:
    return Transcript(id=id_, source="test", turns=[
        Turn(idx=0, speaker="agent", text="Hi"),
        Turn(idx=1, speaker="customer", text="?"),
    ])


class _CountingJudge:
    """A judge that counts calls and can be told to fail/sleep, for deterministic
    tests. Reuses the shape of HeuristicJudge to keep the store happy."""
    def __init__(self, delay: float = 0.0, fail_every: int = 0):
        self.name = "counting"
        self.delay = delay
        self.fail_every = fail_every
        self.calls = 0
        self._heuristic = HeuristicJudge()

    def judge(self, t):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_every and self.calls % self.fail_every == 0:
            raise RuntimeError(f"planned failure #{self.calls}")
        return self._heuristic.judge(t)


class _AlwaysFailJudge:
    name = "always-fail"
    def judge(self, t):
        raise RuntimeError("always fails")


class _JudgeErrorJudge:
    """Judge that raises a specific JudgeError subclass every call."""
    name = "judge-error-emitter"
    def __init__(self, exc_cls, attempts=1):
        self._cls = exc_cls
        self._attempts = attempts

    def judge(self, t):
        raise self._cls(f"planned {self._cls.__name__}", attempts=self._attempts)


@pytest.fixture
def runner(tmp_path):
    """Runner factory for tests. Caller starts/stops in async body."""
    def _make(judge_factory, concurrency=2, max_jobs_kept=100, circuit_threshold=5):
        store = Store(tmp_path / "reviews.json")
        return JobRunner(judge_factory=judge_factory, store=store,
                         concurrency=concurrency, max_jobs_kept=max_jobs_kept,
                         circuit_threshold=circuit_threshold)
    return _make


class TestSubmit:
    @pytest.mark.asyncio
    async def test_returns_queued_job(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            job = r.submit([_t("s1"), _t("s2")])
            assert job.total == 2
            assert job.status in (JobStatus.queued, JobStatus.running)
            assert isinstance(job.id, str) and len(job.id) > 0
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_empty_batch_marks_completed(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            job = r.submit([])
            assert job.status == JobStatus.completed
            assert job.completed_at is not None
        finally:
            await r.stop()


class TestWorkers:
    @pytest.mark.asyncio
    async def test_workers_process_end_to_end(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            job = r.submit([_t("w1"), _t("w2"), _t("w3")])
            final = await wait_for_job(r, job.id, timeout_s=3.0)
            assert final is not None
            assert final.status == JobStatus.completed
            assert final.completed == 3
            assert final.errors == []
            reviews = r.reviews_for(job.id)
            assert {rv.transcript_id for rv in reviews} == {"w1", "w2", "w3"}
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_per_item_failure_recorded_not_raised(self, runner):
        judge = _CountingJudge(fail_every=2)
        r = runner(lambda: judge, concurrency=1)
        await r.start()
        try:
            job = r.submit([_t("a"), _t("b"), _t("c"), _t("d")])
            final = await wait_for_job(r, job.id, timeout_s=3.0)
            assert final.status == JobStatus.completed
            assert final.completed == 4
            assert len(final.errors) == 2
            assert all(e.retryable for e in final.errors)
        finally:
            await r.stop()


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_key_returns_same_job(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            j1 = r.submit([_t("x")], idempotency_key="abc")
            j2 = r.submit([_t("x")], idempotency_key="abc")
            assert j1.id == j2.id
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_different_key_creates_different_job(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            j1 = r.submit([_t("x")], idempotency_key="k1")
            j2 = r.submit([_t("x")], idempotency_key="k2")
            assert j1.id != j2.id
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_no_key_no_dedup(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            j1 = r.submit([_t("x")])
            j2 = r.submit([_t("x")])
            assert j1.id != j2.id
        finally:
            await r.stop()


class TestEviction:
    @pytest.mark.asyncio
    async def test_completed_jobs_evicted_beyond_cap(self, runner):
        r = runner(lambda: HeuristicJudge(), max_jobs_kept=3)
        await r.start()
        try:
            ids = []
            for i in range(5):
                job = r.submit([_t(f"e{i}")])
                await wait_for_job(r, job.id, timeout_s=3.0)
                ids.append(job.id)
            remaining = {j.id for j in r.list_jobs()}
            assert len(remaining) == 3
            assert ids[0] not in remaining  # oldest gone
            assert ids[-1] in remaining     # newest kept
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_idempotency_map_cleaned_on_eviction(self, runner):
        r = runner(lambda: HeuristicJudge(), max_jobs_kept=1)
        await r.start()
        try:
            j1 = r.submit([_t("x")], idempotency_key="cleanme")
            await wait_for_job(r, j1.id, timeout_s=3.0)
            # Submit another to force eviction of j1.
            j2 = r.submit([_t("y")])
            await wait_for_job(r, j2.id, timeout_s=3.0)
            # Same key should now create a new job, not return the evicted one.
            j3 = r.submit([_t("x")], idempotency_key="cleanme")
            assert j3.id != j1.id
        finally:
            await r.stop()


class TestJudgeErrorHandling:
    """Part 2: classified failures should shape the job outcome differently.
    Config-scope aborts the whole job; transcript-scope terminal errors are
    recorded per-item without counting against the circuit; retryable
    failures count normally toward the circuit threshold."""

    @pytest.mark.asyncio
    async def test_config_scope_error_aborts_job(self, runner):
        from cqr.errors import JudgeRejected
        r = runner(lambda: _JudgeErrorJudge(JudgeRejected, attempts=1), concurrency=1)
        await r.start()
        try:
            job = r.submit([_t(f"j{i}") for i in range(4)])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            assert final.status == JobStatus.failed
            # After the first item flips the job, the remaining three drain
            # without hitting the judge; total completed still equals total.
            assert final.completed == 4
            # Only the first item logged a JudgeRejected — the rest are
            # silently drained (job.status == failed short-circuit).
            rejected_errors = [e for e in final.errors if e.error_type == "JudgeRejected"]
            assert len(rejected_errors) == 1
            assert rejected_errors[0].retryable is False
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_transcript_terminal_error_not_counted_in_circuit(self, runner):
        """TranscriptRejected is per-item and non-retryable: the item fails
        but the circuit doesn't open, so remaining items still get judged."""
        from cqr.errors import TranscriptRejected

        class _Mixed:
            name = "mixed"
            def __init__(self):
                self.calls = 0
                self._h = HeuristicJudge()
            def judge(self, t):
                self.calls += 1
                # First 6 items are rejected transcripts; the 7th succeeds.
                # A retryable circuit at threshold=3 would open after 3;
                # since these are terminal-not-retryable, it should NOT open.
                if self.calls <= 6:
                    raise TranscriptRejected(f"reject #{self.calls}", attempts=1)
                return self._h.judge(t)

        judge = _Mixed()
        r = runner(lambda: judge, concurrency=1, circuit_threshold=3)
        await r.start()
        try:
            job = r.submit([_t(f"m{i}") for i in range(7)])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            assert final.status == JobStatus.completed
            # 6 TranscriptRejected + 0 CircuitOpen + 1 success -> 6 errors total.
            assert len(final.errors) == 6
            assert all(e.error_type == "TranscriptRejected" for e in final.errors)
            assert all(e.retryable is False for e in final.errors)
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_retryable_judge_error_counts_toward_circuit(self, runner):
        from cqr.errors import JudgeUnavailable
        r = runner(lambda: _JudgeErrorJudge(JudgeUnavailable), concurrency=1, circuit_threshold=2)
        await r.start()
        try:
            job = r.submit([_t(f"u{i}") for i in range(5)])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            assert final.status == JobStatus.completed
            types = [e.error_type for e in final.errors]
            assert types.count("JudgeUnavailable") == 2  # 2 real attempts before open
            assert types.count("CircuitOpen") == 3       # remaining short-circuit
        finally:
            await r.stop()


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_shorts_remaining_after_threshold(self, runner):
        r = runner(lambda: _AlwaysFailJudge(), concurrency=1, circuit_threshold=3)
        await r.start()
        try:
            job = r.submit([_t(f"c{i}") for i in range(8)])
            final = await wait_for_job(r, job.id, timeout_s=3.0)
            assert final.status == JobStatus.completed
            assert final.completed == 8
            # First 3 are RuntimeError; the remaining 5 short-circuit as CircuitOpen.
            error_types = [e.error_type for e in final.errors]
            assert error_types.count("CircuitOpen") == 5
            assert error_types.count("RuntimeError") == 3
        finally:
            await r.stop()


class TestJobUsageSum:
    @pytest.mark.asyncio
    async def test_job_usage_sums_across_reviews(self, runner):
        """When judge reviews carry `usage`, the JobRunner sums them into
        `job.usage`. Heuristic reviews carry no usage (None); LLM reviews do."""
        from cqr.schema import Usage

        class _WithUsage:
            name = "with-usage"
            def __init__(self):
                self._h = HeuristicJudge()
            def judge(self, t):
                r = self._h.judge(t)
                r.usage = Usage(input_tokens=10, output_tokens=5,
                                cache_read_input_tokens=8, cost_usd=0.0001)
                return r

        r = runner(lambda: _WithUsage(), concurrency=1)
        await r.start()
        try:
            job = r.submit([_t("u1"), _t("u2"), _t("u3")])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            assert final.status == JobStatus.completed
            assert final.usage.input_tokens == 30
            assert final.usage.output_tokens == 15
            assert final.usage.cache_read_input_tokens == 24
            assert final.usage.cost_usd == 0.0003
        finally:
            await r.stop()


class TestIntentOrdering:
    @pytest.mark.asyncio
    async def test_submit_dispatches_in_intent_order(self, runner):
        """Batch order is (intent, id), so consecutive judge calls share the
        cacheable reference prefix. Result set is identical regardless."""
        seen: list[str] = []

        class _Recording:
            name = "recording"
            def judge(self, t):
                seen.append(t.intent or "")
                return HeuristicJudge().judge(t)

        r = runner(lambda: _Recording(), concurrency=1)
        await r.start()
        try:
            unsorted = [
                Transcript(id=f"z{i}", source="test", intent=intent,
                           turns=[Turn(idx=0, speaker="agent", text="hi"),
                                  Turn(idx=1, speaker="customer", text="?")])
                for i, intent in enumerate([
                    "shipping_issue/missing",
                    "account_access/recover_password",
                    "shipping_issue/missing",
                ])
            ]
            job = r.submit(unsorted)
            await wait_for_job(r, job.id, timeout_s=2.0)
            assert seen == [
                "account_access/recover_password",
                "shipping_issue/missing",
                "shipping_issue/missing",
            ]
        finally:
            await r.stop()


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_bounded_by_worker_count(self, runner):
        """4 workers, 8 items @ 0.1s each -> ~0.2s (2 batches). Comfortably < 0.5s."""
        judge = _CountingJudge(delay=0.1)
        r = runner(lambda: judge, concurrency=4)
        await r.start()
        try:
            start = time.monotonic()
            job = r.submit([_t(f"p{i}") for i in range(8)])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            elapsed = time.monotonic() - start
            assert final.status == JobStatus.completed
            assert final.completed == 8
            assert judge.calls == 8
            assert elapsed < 0.5, f"expected < 0.5s with 4 workers, got {elapsed:.2f}s"
        finally:
            await r.stop()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_double_start_is_noop(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        await r.start()  # should not spawn extra workers
        try:
            assert len(r._workers) == 2  # constructor default in fixture
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_worker_factory_failure_swallowed(self, runner):
        """If the judge factory can't build (e.g. bad config), the worker just
        exits — submits go into the queue and stall, but nothing crashes."""
        def bad_factory():
            raise RuntimeError("no key")
        r = runner(bad_factory)
        await r.start()
        try:
            job = r.submit([_t("stuck")])
            # No worker will pick this up — job stays queued.
            await asyncio.sleep(0.1)
            assert r.get(job.id).status == JobStatus.queued
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_wait_for_job_none_when_missing(self, runner):
        r = runner(lambda: HeuristicJudge())
        await r.start()
        try:
            assert await wait_for_job(r, "no-such-id", timeout_s=0.1) is None
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_wait_for_job_returns_running_on_timeout(self, runner):
        """Timeout expires before completion → return the still-running snapshot."""
        judge = _CountingJudge(delay=0.3)
        r = runner(lambda: judge, concurrency=1)
        await r.start()
        try:
            job = r.submit([_t("slow-1"), _t("slow-2")])
            snap = await wait_for_job(r, job.id, timeout_s=0.05)
            assert snap is not None
            assert snap.status in (JobStatus.queued, JobStatus.running)
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_process_one_skips_evicted_job(self, runner):
        """Direct-call cover: if the job was evicted before processing starts,
        _process_one returns early."""
        r = runner(lambda: HeuristicJudge(), concurrency=1)
        await r.start()
        try:
            job = r.submit([_t("gone")])
            await wait_for_job(r, job.id, timeout_s=1.0)
            r._jobs.pop(job.id)  # simulate eviction
            # Now call _process_one directly — should be a no-op.
            await r._process_one(HeuristicJudge(), job.id, _t("gone"))
            # No exception; nothing added.
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_process_one_skips_failed_job(self, runner):
        """If a job is already marked failed (Part 2 will do this on config
        errors), remaining items are silently drained."""
        r = runner(lambda: HeuristicJudge(), concurrency=1)
        await r.start()
        try:
            job = r.submit([_t("f1")])
            await wait_for_job(r, job.id, timeout_s=1.0)
            job.status = JobStatus.failed
            initial_completed = job.completed
            await r._process_one(HeuristicJudge(), job.id, _t("would-be"))
            assert job.completed == initial_completed + 1
        finally:
            await r.stop()

    @pytest.mark.asyncio
    async def test_eviction_bail_when_all_in_flight(self, runner):
        """If every remembered job is queued/running, eviction can't do anything."""
        from cqr.schema import Job as _Job
        r = runner(lambda: HeuristicJudge(), max_jobs_kept=1)
        await r.start()
        try:
            r._jobs["synthetic-1"] = _Job(id="synthetic-1", total=1)  # queued
            r._jobs["synthetic-2"] = _Job(id="synthetic-2", total=1)  # queued
            before = len(r._jobs)
            r._evict_if_needed()
            assert len(r._jobs) == before  # nothing evictable
        finally:
            await r.stop()
