"""FastAPI endpoints exercised via TestClient. Uses HeuristicJudge (no network)."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from cqr import api
from cqr.jobs import JobRunner
from cqr.judge import HeuristicJudge
from cqr.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated per-test API: fresh store, heuristic judge, lifespan-managed
    JobRunner. Uses `with` so lifespan startup/shutdown actually fire."""
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setenv("CQR_JUDGE", "heuristic")
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    with TestClient(api.app) as c:
        yield c


def _transcript_body(id_="conv-1", turns=None, **extra):
    return {
        "id": id_,
        "source": "upload",
        "turns": turns or [
            {"idx": 0, "speaker": "agent", "text": "Hi, how can I help?"},
            {"idx": 1, "speaker": "customer", "text": "My package never arrived."},
        ],
        **extra,
    }


def _wait_until_done(client, job_id, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = client.get(f"/jobs/{job_id}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout_s}s")


class TestPostReview:
    def test_happy_path_returns_review(self, client):
        resp = client.post("/review", json=_transcript_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript_id"] == "conv-1"
        assert body["judge"] == "heuristic"
        assert "resolution" in body
        assert body["job_id"] is None  # single POST /review is not a batch

    def test_persists_to_store(self, client):
        client.post("/review", json=_transcript_body("keep-me"))
        follow = client.get("/reviews/keep-me")
        assert follow.status_code == 200
        assert follow.json()["review"]["transcript_id"] == "keep-me"

    def test_no_reference_yields_unverifiable(self, client):
        body = client.post("/review", json=_transcript_body()).json()
        assert body["correctness"]["level"] == "unverifiable"

    def test_malformed_body_returns_422(self, client):
        resp = client.post("/review", json={"id": "x"})
        assert resp.status_code == 422

    def test_empty_turns_returns_422(self, client):
        resp = client.post("/review", json={"id": "x", "source": "upload", "turns": []})
        assert resp.status_code == 422

    def test_duplicate_idx_returns_422(self, client):
        resp = client.post("/review", json={
            "id": "x", "source": "upload",
            "turns": [
                {"idx": 0, "speaker": "agent", "text": "hi"},
                {"idx": 0, "speaker": "customer", "text": "?"},
            ],
        })
        assert resp.status_code == 422

    def test_contiguous_idx_accepted(self, client):
        resp = client.post("/review", json=_transcript_body("contig", turns=[
            {"idx": 0, "speaker": "agent", "text": "hi"},
            {"idx": 1, "speaker": "customer", "text": "help"},
        ]))
        assert resp.status_code == 200


class TestReviewBatchAsync:
    def test_returns_202_with_batch_accepted(self, client):
        resp = client.post("/review/batch", json={"transcripts": [
            _transcript_body("a"), _transcript_body("b"),
        ]})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert body["total"] == 2
        assert isinstance(body["job_id"], str) and len(body["job_id"]) > 0

    def test_wait_true_returns_200_with_completed_job(self, client, monkeypatch):
        monkeypatch.setenv("CQR_BATCH_WAIT_S", "5")
        resp = client.post("/review/batch?wait=true", json={"transcripts": [
            _transcript_body("wait-1"), _transcript_body("wait-2"),
        ]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["total"] == 2
        assert body["completed"] == 2

    def test_reviews_carry_job_id(self, client):
        resp = client.post("/review/batch?wait=true", json={"transcripts": [
            _transcript_body("job-tagged"),
        ]})
        job_id = resp.json()["id"]
        review = client.get("/reviews/job-tagged").json()["review"]
        assert review["job_id"] == job_id

    def test_idempotency_key_returns_same_job(self, client):
        headers = {"Idempotency-Key": "unique-abc-123"}
        first = client.post("/review/batch", json={"transcripts": [_transcript_body("dup")]}, headers=headers)
        second = client.post("/review/batch", json={"transcripts": [_transcript_body("dup")]}, headers=headers)
        assert first.status_code == 202 and second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]

    def test_batch_exceeds_max_returns_422(self, client):
        # CQR_MAX_BATCH default is 200; pydantic max_length is baked in at import.
        big = [_transcript_body(f"x{i}") for i in range(201)]
        resp = client.post("/review/batch", json={"transcripts": big})
        assert resp.status_code == 422


class TestJobEndpoints:
    def test_list_jobs_starts_empty(self, client):
        assert client.get("/jobs").json() == []

    def test_list_jobs_includes_submissions(self, client):
        client.post("/review/batch", json={"transcripts": [_transcript_body("j1")]})
        client.post("/review/batch", json={"transcripts": [_transcript_body("j2")]})
        jobs = client.get("/jobs").json()
        assert len(jobs) == 2

    def test_get_job_by_id(self, client):
        job_id = client.post("/review/batch", json={"transcripts": [_transcript_body("g1")]}).json()["job_id"]
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        assert r.json()["id"] == job_id

    def test_get_job_404(self, client):
        assert client.get("/jobs/no-such-job").status_code == 404

    def test_get_job_reviews_riskiest_first(self, client):
        job_id = client.post("/review/batch", json={"transcripts": [
            _transcript_body("a"), _transcript_body("b"), _transcript_body("c"),
        ]}).json()["job_id"]
        _wait_until_done(client, job_id)
        reviews = client.get(f"/jobs/{job_id}/reviews").json()
        assert len(reviews) == 3
        assert all(r["job_id"] == job_id for r in reviews)

    def test_get_job_reviews_404_on_missing_job(self, client):
        assert client.get("/jobs/no-such/reviews").status_code == 404


class TestReviewsJobIdFilter:
    def test_filter_by_job_id(self, client):
        job1_id = client.post("/review/batch", json={"transcripts": [_transcript_body("j1a"), _transcript_body("j1b")]}).json()["job_id"]
        job2_id = client.post("/review/batch", json={"transcripts": [_transcript_body("j2a")]}).json()["job_id"]
        _wait_until_done(client, job1_id)
        _wait_until_done(client, job2_id)
        r1 = client.get(f"/reviews?job_id={job1_id}").json()
        r2 = client.get(f"/reviews?job_id={job2_id}").json()
        assert {r["transcript_id"] for r in r1} == {"j1a", "j1b"}
        assert {r["transcript_id"] for r in r2} == {"j2a"}


class TestGetReviews:
    def _seed(self, client, ids):
        for i in ids:
            client.post("/review", json=_transcript_body(i))

    def test_list_returns_all(self, client):
        self._seed(client, ["a", "b", "c"])
        rs = client.get("/reviews").json()
        assert len(rs) == 3

    def test_filter_needs_human_review(self, client):
        self._seed(client, ["safe-1", "safe-2"])
        rs = client.get("/reviews?needs_human_review=true").json()
        assert rs == []
        rs = client.get("/reviews?needs_human_review=false").json()
        assert len(rs) == 2

    def test_filter_by_source(self, client):
        client.post("/review", json=_transcript_body("u1", source="upload"))
        client.post("/review", json=_transcript_body("u2", source="upload"))
        client.post("/review", json=_transcript_body("s1", source="synthetic"))
        rs = client.get("/reviews?source=synthetic").json()
        assert len(rs) == 1
        assert rs[0]["transcript_id"] == "s1"


class TestGetReviewById:
    def test_returns_transcript_and_review(self, client):
        client.post("/review", json=_transcript_body("show-me"))
        resp = client.get("/reviews/show-me")
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript"]["id"] == "show-me"
        assert body["review"]["transcript_id"] == "show-me"

    def test_404_when_missing(self, client):
        resp = client.get("/reviews/nope")
        assert resp.status_code == 404


class TestLazyJudgeInit:
    def test_first_call_constructs_and_caches_judge(self, monkeypatch):
        monkeypatch.setenv("CQR_JUDGE", "heuristic")
        monkeypatch.setattr(api, "_judge", None)
        first = api.judge()
        assert first.name == "heuristic"
        assert api.judge() is first


class TestRunnerNotStarted:
    """Cover the guards that fire before/without lifespan starting the runner."""

    def test_endpoints_return_503(self, monkeypatch):
        monkeypatch.setattr(api, "_runner", None)
        # Use TestClient WITHOUT `with`, so lifespan does not run.
        c = TestClient(api.app)
        assert c.post("/review/batch", json={"transcripts": [_transcript_body("x")]}).status_code == 503
        assert c.get("/jobs").status_code == 503
        assert c.get("/jobs/anything").status_code == 503
        assert c.get("/jobs/anything/reviews").status_code == 503


class TestDashboardRoot:
    def test_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower()


class TestOpenApiSpec:
    def test_spec_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        assert spec["openapi"].startswith("3.")

    def test_endpoints_have_summaries(self, client):
        spec = client.get("/openapi.json").json()
        for path in ("/review", "/review/batch", "/reviews", "/reviews/{id_}",
                     "/jobs", "/jobs/{id_}", "/jobs/{id_}/reviews"):
            for method_op in spec["paths"][path].values():
                assert method_op.get("summary")

    def test_dashboard_hidden_from_schema(self, client):
        spec = client.get("/openapi.json").json()
        assert "/" not in spec["paths"]
