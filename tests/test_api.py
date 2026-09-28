"""FastAPI endpoints exercised via TestClient. Uses HeuristicJudge (no network)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cqr import api
from cqr.judge import HeuristicJudge
from cqr.store import Store


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolated per-test API: fresh store, heuristic judge, TestClient."""
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    return TestClient(api.app)


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


class TestPostReview:
    def test_happy_path_returns_review(self, client):
        resp = client.post("/review", json=_transcript_body())
        assert resp.status_code == 200
        body = resp.json()
        assert body["transcript_id"] == "conv-1"
        assert body["judge"] == "heuristic"
        assert "resolution" in body
        assert "correctness" in body

    def test_persists_to_store(self, client):
        client.post("/review", json=_transcript_body("keep-me"))
        follow = client.get("/reviews/keep-me")
        assert follow.status_code == 200
        assert follow.json()["review"]["transcript_id"] == "keep-me"

    def test_no_reference_yields_unverifiable(self, client):
        body = client.post("/review", json=_transcript_body()).json()
        assert body["correctness"]["level"] == "unverifiable"

    def test_malformed_body_returns_422(self, client):
        resp = client.post("/review", json={"id": "x"})  # missing required fields
        assert resp.status_code == 422


class TestPostReviewBatch:
    def test_returns_reviews_and_empty_errors_on_all_ok(self, client):
        resp = client.post("/review/batch", json={"transcripts": [
            _transcript_body("a"), _transcript_body("b"),
        ]})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["reviews"]) == 2
        assert body["errors"] == []

    def test_partial_failure_isolates_per_transcript(self, client, monkeypatch):
        """One bad judge call must not abort the batch."""
        real_judge = HeuristicJudge()
        original_judge_fn = real_judge.judge

        def flaky(t):
            if t.id == "bad":
                raise RuntimeError("simulated judge failure")
            return original_judge_fn(t)

        real_judge.judge = flaky
        monkeypatch.setattr(api, "_judge", real_judge)

        resp = client.post("/review/batch", json={"transcripts": [
            _transcript_body("ok-1"), _transcript_body("bad"), _transcript_body("ok-2"),
        ]})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["reviews"]) == 2
        assert {r["transcript_id"] for r in body["reviews"]} == {"ok-1", "ok-2"}
        assert len(body["errors"]) == 1
        assert body["errors"][0]["transcript_id"] == "bad"
        assert "RuntimeError" in body["errors"][0]["error"]


class TestGetReviews:
    def _seed(self, client, ids):
        for i in ids:
            client.post("/review", json=_transcript_body(i))

    def test_list_returns_all(self, client):
        self._seed(client, ["a", "b", "c"])
        rs = client.get("/reviews").json()
        assert len(rs) == 3

    def test_filter_needs_human_review(self, client):
        # Neither of these will trigger needs_human_review under the heuristic.
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
        assert api.judge() is first  # cached


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
        for path in ("/review", "/review/batch", "/reviews", "/reviews/{id_}"):
            for method_op in spec["paths"][path].values():
                assert method_op.get("summary")

    def test_dashboard_hidden_from_schema(self, client):
        spec = client.get("/openapi.json").json()
        assert "/" not in spec["paths"]
