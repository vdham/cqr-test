"""
Spec-vs-code parity for the API's error contract.

Three checks:

1. `EXPECTED_MATRIX` is hard-coded in this file. `app.openapi()` must
   declare exactly those (path, method, status) triples. A drift in
   either direction fails.
2. Drive each declared error status against a stubbed judge/runner and
   assert the response matches the contract (status, ErrorBody shape,
   `error_type`, `retryable`, `Retry-After` header exactly when
   declared).
3. A drift-tracking ASGI middleware (installed in conftest.py) records
   every (route, method, status) the app emitted during the session.
   The final test asserts the observed set has no status codes absent
   from the spec — this catches a future `raise HTTPException(409)`
   that someone forgot to declare.

Plus: `python scripts/render_error_table.py` output must match the
README's `<!-- ERROR-TABLE-START -->..<!-- ERROR-TABLE-END -->` region
byte-for-byte.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqr import api
from cqr.errors import (JudgeError, JudgeOutputInvalid, JudgeRejected,
                        JudgeUnavailable, TranscriptRejected)
from cqr.jobs import JobRunner
from cqr.judge import HeuristicJudge
from cqr.schema import ErrorBody
from cqr.store import Store


# ---------------------------------------------------- expected spec matrix ----

# `(method, path) -> set[status]`. The tests below assert app.openapi() lists
# EXACTLY these statuses per route. Change here + change ERROR_RESPONSES in
# api.py + change README — the tests will catch any two-of-three drift.
EXPECTED_MATRIX: dict[tuple[str, str], set[int]] = {
    ("POST", "/review"):             {200, 413, 422, 500, 502, 503},
    ("POST", "/review/batch"):       {200, 202, 413, 422, 429},
    ("GET",  "/jobs"):               {200},
    ("GET",  "/jobs/{id_}"):         {200, 404, 422},   # 422 is FastAPI path-param validation
    ("GET",  "/jobs/{id_}/reviews"): {200, 404, 422},
    ("GET",  "/reviews"):            {200, 422},        # 422 is FastAPI query-param validation
    ("GET",  "/reviews/{id_}"):      {200, 404, 422},
    ("GET",  "/guidelines"):         {200},
    ("GET",  "/guidelines/{flow_key}/{subflow_key}"): {200, 404, 422},
    ("GET",  "/health"):             {200},
}


# --------------------------------------------------------------- fixtures ----

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setenv("CQR_JUDGE", "heuristic")
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    with TestClient(api.app) as c:
        yield c


def _install_failing_judge(monkeypatch, exc_cls, attempts=2):
    class _Judge:
        name = "test"
        def judge(self, t):
            exc = exc_cls("planned failure")
            exc.attempts = attempts
            raise exc
    monkeypatch.setattr(api, "_judge", _Judge())


def _minimal_body(id_="c1"):
    return {"id": id_, "source": "upload", "turns": [
        {"idx": 0, "speaker": "agent", "text": "hi"},
        {"idx": 1, "speaker": "customer", "text": "help"},
    ]}


# =================================== 1. spec-parity =============================

class TestOpenApiParity:
    def test_matrix_matches_spec(self, client):
        spec = client.get("/openapi.json").json()
        declared: dict[tuple[str, str], set[int]] = {}
        for path, methods in spec["paths"].items():
            for method, op in methods.items():
                key = (method.upper(), path)
                declared[key] = {int(c) for c in op["responses"]}
        assert declared == EXPECTED_MATRIX, (
            f"OpenAPI drift.\n"
            f"declared  ={sorted(declared.items())}\n"
            f"expected  ={sorted(EXPECTED_MATRIX.items())}"
        )

    def test_status_set_matches_acceptance(self, client):
        spec = client.get("/openapi.json").json()
        all_codes = sorted({c for p in spec["paths"].values()
                              for m in p.values()
                              for c in m["responses"]})
        assert all_codes == ["200", "202", "404", "413", "422", "429", "500", "502", "503"]

    def test_503_documents_retry_after(self, client):
        spec = client.get("/openapi.json").json()
        r = spec["paths"]["/review"]["post"]["responses"]["503"]
        assert "Retry-After" in r.get("headers", {})

    def test_429_documents_retry_after(self, client):
        spec = client.get("/openapi.json").json()
        r = spec["paths"]["/review/batch"]["post"]["responses"]["429"]
        assert "Retry-After" in r.get("headers", {})

    def test_error_body_in_schemas(self, client):
        spec = client.get("/openapi.json").json()
        assert "ErrorBody" in spec["components"]["schemas"]


# ================================= 2. per-status drivers ========================

class TestErrorDrivers:
    """Every declared error status must be reachable, return ErrorBody, and
    (for retryable ones) carry Retry-After."""

    def _assert_error_body(self, resp, error_type, retryable, has_retry_after):
        body = resp.json()
        ErrorBody.model_validate(body)  # shape check
        assert body["error_type"] == error_type
        assert body["retryable"] is retryable
        if has_retry_after:
            assert resp.headers.get("retry-after") == "30"
            assert body["retry_after_s"] == 30
        else:
            assert "retry-after" not in {k.lower() for k in resp.headers}

    def test_503_judge_unavailable(self, client, monkeypatch):
        _install_failing_judge(monkeypatch, JudgeUnavailable)
        r = client.post("/review", json=_minimal_body())
        assert r.status_code == 503
        self._assert_error_body(r, "JudgeUnavailable", retryable=True, has_retry_after=True)

    def test_502_judge_rejected(self, client, monkeypatch):
        _install_failing_judge(monkeypatch, JudgeRejected)
        r = client.post("/review", json=_minimal_body())
        assert r.status_code == 502
        self._assert_error_body(r, "JudgeRejected", retryable=False, has_retry_after=False)

    def test_500_judge_output_invalid(self, client, monkeypatch):
        _install_failing_judge(monkeypatch, JudgeOutputInvalid)
        r = client.post("/review", json=_minimal_body())
        assert r.status_code == 500
        self._assert_error_body(r, "JudgeOutputInvalid", retryable=False, has_retry_after=False)

    def test_422_transcript_rejected(self, client, monkeypatch):
        _install_failing_judge(monkeypatch, TranscriptRejected)
        r = client.post("/review", json=_minimal_body())
        assert r.status_code == 422
        # TranscriptRejected uses ErrorBody shape (unlike FastAPI's own 422).
        self._assert_error_body(r, "TranscriptRejected", retryable=False, has_retry_after=False)

    def test_422_validation_uses_fastapi_shape(self, client):
        r = client.post("/review", json={"id": "x"})  # missing required fields
        assert r.status_code == 422
        # FastAPI's native shape: {"detail": [...]} — NOT ErrorBody.
        body = r.json()
        assert "detail" in body
        assert isinstance(body["detail"], list)

    def test_413_payload_too_large(self, client, monkeypatch):
        monkeypatch.setattr(api, "_MAX_BODY_BYTES", 100)  # tiny cap so anything trips it
        r = client.post("/review", json=_minimal_body(),
                        headers={"content-length": "10000"})
        assert r.status_code == 413
        self._assert_error_body(r, "PayloadTooLarge", retryable=False, has_retry_after=False)

    def test_429_queue_full(self, client, monkeypatch):
        # Rebuild the runner with a tiny queue, then submit past capacity.
        tiny = JobRunner(judge_factory=HeuristicJudge, store=api._store,
                         concurrency=0, max_queue_size=1)
        # concurrency=0 -> min-clamped to 1, but we won't start it, so nothing drains.
        monkeypatch.setattr(api, "_runner", tiny)
        # First submit fills capacity (1 item).
        r1 = client.post("/review/batch",
                         json={"transcripts": [_minimal_body("q1")]})
        assert r1.status_code == 202
        # Second submit overflows.
        r2 = client.post("/review/batch",
                         json={"transcripts": [_minimal_body("q2")]})
        assert r2.status_code == 429
        self._assert_error_body(r2, "QueueFull", retryable=True, has_retry_after=True)

    def test_404_missing_job(self, client):
        r = client.get("/jobs/no-such-job")
        assert r.status_code == 404
        self._assert_error_body(r, "NotFound", retryable=False, has_retry_after=False)

    def test_404_missing_review(self, client):
        r = client.get("/reviews/no-such-review")
        assert r.status_code == 404
        self._assert_error_body(r, "NotFound", retryable=False, has_retry_after=False)

    def test_404_missing_job_reviews(self, client):
        r = client.get("/jobs/no-such/reviews")
        assert r.status_code == 404
        self._assert_error_body(r, "NotFound", retryable=False, has_retry_after=False)

    def test_413_missing_content_length_passes(self, client):
        # No Content-Length -> the middleware lets it through.
        r = client.post("/review", json=_minimal_body())
        assert r.status_code == 200

    def test_413_malformed_content_length_passes(self, client, monkeypatch):
        """A bogus Content-Length header shouldn't 500 — treat as unknown and
        let the request through (the actual body size, once parsed, may still
        fail validation elsewhere)."""
        monkeypatch.setattr(api, "_MAX_BODY_BYTES", 100)
        r = client.post("/review", json=_minimal_body(),
                        headers={"content-length": "not-a-number"})
        # Passes the middleware (n=0), then reaches the endpoint and succeeds.
        assert r.status_code == 200


# ================================== 3. render script parity =====================

def test_render_matches_readme():
    """The section between the ERROR-TABLE markers in README.md must be
    byte-identical to `scripts/render_error_table.py` output."""
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "render_error_table.py")],
        capture_output=True, check=True, text=True,
    )
    rendered = result.stdout

    readme = (repo / "README.md").read_text()
    start = readme.index("<!-- ERROR-TABLE-START -->")
    end = readme.index("<!-- ERROR-TABLE-END -->")
    # Extract just the payload between the markers (exclusive of the markers themselves).
    payload = readme[start + len("<!-- ERROR-TABLE-START -->"):end].strip("\n") + "\n"
    assert payload == rendered, (
        "README's error-contract section drifted from the render script. "
        "Regenerate with: python scripts/render_error_table.py"
    )


# ============================== 4. drift middleware assertion ==================
# Installed globally in conftest.py; the assertion runs last so every other
# test's traffic has been recorded before we check.

class TestDriftCheck:
    def test_no_undeclared_statuses_emitted(self, client):
        # Exercise something so the observed set isn't empty for THIS session.
        client.get("/health")
        # Import the shared observed set from conftest.
        from tests.conftest import OBSERVED_RESPONSES
        # Any observed (method, path_template, status) whose path is in the
        # spec must have that status declared for that (method, path).
        undeclared = []
        for method, path, status in OBSERVED_RESPONSES:
            expected = EXPECTED_MATRIX.get((method, path))
            if expected is None:
                # Route not in the spec (dashboard, openapi.json). Skip.
                continue
            if status not in expected:
                undeclared.append((method, path, status))
        assert not undeclared, (
            f"App emitted statuses not declared in the OpenAPI spec:\n"
            f"  {sorted(undeclared)}\n"
            f"Add them to ERROR_RESPONSES + EXPECTED_MATRIX (or fix the code)."
        )
