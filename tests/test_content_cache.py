"""Content-addressed identity: transcript digest, cache hits, force bypass."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from cqr import api
from cqr.judge import HeuristicJudge
from cqr.rubric import RUBRIC_VERSION
from cqr.schema import SCHEMA_VERSION, Transcript, Turn, reference_version
from cqr.store import Store


# ---------------------------------------------------------------- Digest ---

class TestTranscriptDigest:
    def _t(self, **overrides):
        base = dict(
            id="c1", source="upload", intent="shipping_issue/missing",
            reference="AGENT GUIDELINES: reship after 7 days.",
            turns=[Turn(idx=0, speaker="agent", text="hi"),
                   Turn(idx=1, speaker="customer", text="?")],
        )
        base.update(overrides)
        return Transcript(**base)

    def test_digest_is_deterministic(self):
        assert self._t().digest() == self._t().digest()

    def test_digest_is_stable_under_key_reordering(self):
        """A canonical-JSON digest doesn't depend on how the caller happened
        to order kwargs. Same content → same digest."""
        a = Transcript(id="c1", source="upload", intent="x",
                       reference="R",
                       turns=[Turn(idx=0, speaker="agent", text="hi")])
        b = Transcript(turns=[Turn(idx=0, speaker="agent", text="hi")],
                       reference="R", intent="x", source="upload", id="c1")
        assert a.digest() == b.digest()

    def test_metadata_does_not_affect_digest(self):
        t1 = self._t()
        t2 = self._t()
        t2.metadata = {"member_level": "gold", "internal_ticket": "abc"}
        assert t1.digest() == t2.digest()

    def test_changing_one_character_changes_digest(self):
        t1 = self._t()
        t2 = self._t()
        t2.turns[1].text = "??"  # was "?"
        assert t1.digest() != t2.digest()

    def test_intent_change_changes_digest(self):
        assert self._t(intent="shipping/missing").digest() != self._t(intent="shipping/status").digest()

    def test_reference_change_changes_digest(self):
        assert self._t(reference="policy A").digest() != self._t(reference="policy B").digest()


class TestReferenceVersion:
    def test_none_when_no_reference(self):
        assert reference_version(None) == "none"
        assert reference_version("") == "none"

    def test_short_hash_for_content(self):
        v = reference_version("some policy text")
        assert v != "none"
        assert len(v) == 12
        assert reference_version("some policy text") == v  # deterministic

    def test_different_text_different_hash(self):
        assert reference_version("policy A") != reference_version("policy B")


# ----------------------------------------------------------- Store.find ----

class TestStoreFind:
    def _reviewed(self, tid="a", intent=None, reference=None):
        t = Transcript(id=tid, source="upload", intent=intent, reference=reference,
                       turns=[Turn(idx=0, speaker="agent", text="hi"),
                              Turn(idx=1, speaker="customer", text="?")])
        r = HeuristicJudge().judge(t)
        return t, r

    def test_find_returns_stored_review_on_match(self, tmp_path):
        s = Store(tmp_path / "s.json")
        t, r = self._reviewed()
        s.put(t, r)
        hit = s.find(t.digest(), r.rubric_version, r.reference_version, r.judge)
        assert hit is not None
        assert hit.transcript_id == t.id

    def test_find_returns_none_on_digest_miss(self, tmp_path):
        s = Store(tmp_path / "s.json")
        t, r = self._reviewed()
        s.put(t, r)
        assert s.find("0" * 64, r.rubric_version, r.reference_version, r.judge) is None

    def test_find_scoped_by_rubric_and_reference_and_judge(self, tmp_path):
        s = Store(tmp_path / "s.json")
        t, r = self._reviewed()
        s.put(t, r)
        # Different rubric_version → miss.
        assert s.find(t.digest(), "9.9", r.reference_version, r.judge) is None
        # Different reference_version → miss.
        assert s.find(t.digest(), r.rubric_version, "deadbeef1234", r.judge) is None
        # Different judge → miss.
        assert s.find(t.digest(), r.rubric_version, r.reference_version, "llm:other") is None

    def test_pre_versioning_reviews_not_indexed(self, tmp_path, minimal_review_kwargs):
        """Reviews written before the schema had `transcript_digest` load fine
        but never cache-hit (empty digest can't match a computed one)."""
        from cqr.schema import Review
        s = Store(tmp_path / "s.json")
        old = Review(**minimal_review_kwargs)  # transcript_digest="" by default
        # Simulate a stored pre-versioning review.
        s._reviews["legacy"] = old
        s._index("legacy", old)
        assert s._by_content == {}  # nothing indexed
        # And find() with any digest returns None.
        assert s.find("a" * 64, "1.0", "none", "heuristic") is None

    def test_index_rebuilt_on_load(self, tmp_path):
        p = tmp_path / "s.json"
        s1 = Store(p)
        t, r = self._reviewed()
        s1.put(t, r)
        s1.flush()
        s2 = Store(p)
        hit = s2.find(t.digest(), r.rubric_version, r.reference_version, r.judge)
        assert hit is not None
        assert hit.transcript_id == t.id


# ------------------------------------------------ POST /review cache ------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setenv("CQR_JUDGE", "heuristic")
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    with TestClient(api.app) as c:
        yield c


def _body(id_="c1", **extra):
    return {
        "id": id_,
        "source": "upload",
        "turns": [
            {"idx": 0, "speaker": "agent", "text": "hi"},
            {"idx": 1, "speaker": "customer", "text": "?"},
        ],
        **extra,
    }


class TestPostReviewCache:
    def _install_counting_judge(self, monkeypatch):
        calls = {"n": 0}
        base = HeuristicJudge()

        class _Counting:
            name = "heuristic"
            def judge(self, t):
                calls["n"] += 1
                return base.judge(t)

        monkeypatch.setattr(api, "_judge", _Counting())
        return calls

    def test_second_call_returns_cache_hit(self, client, monkeypatch):
        calls = self._install_counting_judge(monkeypatch)
        r1 = client.post("/review", json=_body("cache-a")).json()
        r2 = client.post("/review", json=_body("cache-a")).json()
        assert calls["n"] == 1
        assert r1["cache_hit"] is False
        assert r2["cache_hit"] is True
        # Same digest + reference + judge → same review payload.
        assert r1["transcript_digest"] == r2["transcript_digest"]
        assert r1["transcript_id"] == r2["transcript_id"]

    def test_force_bypasses_cache(self, client, monkeypatch):
        calls = self._install_counting_judge(monkeypatch)
        client.post("/review", json=_body("cache-b"))
        r2 = client.post("/review?force=true", json=_body("cache-b")).json()
        assert calls["n"] == 2  # judge called both times
        assert r2["cache_hit"] is False

    def test_changing_a_turn_bypasses_cache(self, client, monkeypatch):
        calls = self._install_counting_judge(monkeypatch)
        client.post("/review", json=_body("cache-c"))
        modified = _body("cache-c")
        modified["turns"][1]["text"] = "different text"
        r2 = client.post("/review", json=modified).json()
        assert calls["n"] == 2
        assert r2["cache_hit"] is False

    def test_cache_hit_not_persisted_true(self, client, monkeypatch):
        self._install_counting_judge(monkeypatch)
        client.post("/review", json=_body("cache-d"))
        # Direct store peek — the persisted review is cache_hit=False even
        # after a cache-hit response.
        client.post("/review", json=_body("cache-d"))  # cache hit
        stored = api._store.get("cache-d")[1]
        assert stored.cache_hit is False


class TestJobRunnerCache:
    """Batch flow: cache hits skip the judge and don't accrue usage, but still
    count against Job.completed (the batch made progress on that item)."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_judge_call(self, tmp_path):
        from cqr.jobs import JobRunner, wait_for_job
        from cqr.schema import JobStatus

        counter = {"n": 0}
        base = HeuristicJudge()

        class _Counting:
            name = "heuristic"
            def judge(self, t):
                counter["n"] += 1
                return base.judge(t)

        store = Store(tmp_path / "s.json")
        # Warm the cache with one review.
        t = Transcript(id="warm", source="test", turns=[
            Turn(idx=0, speaker="agent", text="hi"),
            Turn(idx=1, speaker="customer", text="?"),
        ])
        counting = _Counting()
        store.put(t, counting.judge(t))
        assert counter["n"] == 1

        # Submit the same transcript through the runner — should cache-hit.
        r = JobRunner(judge_factory=lambda: counting, store=store, concurrency=1)
        await r.start()
        try:
            job = r.submit([t])
            final = await wait_for_job(r, job.id, timeout_s=2.0)
            assert final.status == JobStatus.completed
            assert final.completed == 1
            assert counter["n"] == 1  # judge NOT called again
        finally:
            await r.stop()


class TestCliForce:
    def test_force_flag_bypasses_cache(self, tmp_path, capsys):
        from cqr import cli
        from cqr.loader import dump_jsonl

        t = Transcript(id="cli-cache", source="synthetic", turns=[
            Turn(idx=0, speaker="agent", text="hi"),
            Turn(idx=1, speaker="customer", text="?"),
        ])
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([t], jsonl)

        # First run: 1 scored, 0 cached.
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        first = capsys.readouterr().err
        assert "1 scored, 0 cached" in first

        # Second run without --force: 0 scored, 1 cached.
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        second = capsys.readouterr().err
        assert "0 scored, 1 cached" in second

        # Third run with --force: 1 scored, 0 cached.
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out), "--force"])
        third = capsys.readouterr().err
        assert "1 scored, 0 cached" in third


class TestReviewFieldsPresent:
    def test_all_new_fields_are_serialized(self, client):
        body = client.post("/review", json=_body("fields")).json()
        assert body["schema_version"] == SCHEMA_VERSION
        assert body["rubric_version"] == RUBRIC_VERSION
        assert body["reference_version"] == "none"  # no reference on this body
        assert isinstance(body["transcript_digest"], str) and len(body["transcript_digest"]) == 64
        assert body["cache_hit"] is False
