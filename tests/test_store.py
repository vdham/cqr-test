"""Store persistence: put/get/all/flush and cold-load round-trip."""
from __future__ import annotations

from cqr.judge import HeuristicJudge
from cqr.store import Store


class TestStore:
    def _judge_and_put(self, store, transcript):
        r = HeuristicJudge().judge(transcript)
        store.put(transcript, r)
        return r

    def test_put_get_round_trip(self, tmp_store, sample_transcript):
        review = self._judge_and_put(tmp_store, sample_transcript)
        hit = tmp_store.get(sample_transcript.id)
        assert hit is not None
        t, r = hit
        assert t.id == sample_transcript.id
        assert r.transcript_id == review.transcript_id

    def test_get_missing_returns_none(self, tmp_store):
        assert tmp_store.get("does-not-exist") is None

    def test_all_returns_every_review(self, tmp_store, sample_transcript, make_turn):
        from cqr.schema import Transcript
        second = Transcript(id="conv-2", source="test",
                            turns=[make_turn(0, "agent", "Hi"),
                                   make_turn(1, "customer", "Help")])
        self._judge_and_put(tmp_store, sample_transcript)
        self._judge_and_put(tmp_store, second)
        assert len(tmp_store.all()) == 2

    def test_flush_persists_to_disk(self, tmp_path, sample_transcript):
        s1 = Store(tmp_path / "r.json")
        self._judge_and_put(s1, sample_transcript)
        s1.flush()
        # Fresh Store re-reads from disk on construction.
        s2 = Store(tmp_path / "r.json")
        assert len(s2.all()) == 1
        hit = s2.get(sample_transcript.id)
        assert hit is not None

    def test_creates_parent_dir(self, tmp_path):
        Store(tmp_path / "nested" / "dir" / "r.json")
        assert (tmp_path / "nested" / "dir").exists()

    def test_overwrite_same_id(self, tmp_store, sample_transcript):
        self._judge_and_put(tmp_store, sample_transcript)
        self._judge_and_put(tmp_store, sample_transcript)
        assert len(tmp_store.all()) == 1
