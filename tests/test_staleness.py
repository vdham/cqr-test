"""Stale-review detection + `cqr rereview`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cqr import api, cli, rubric, schema
from cqr.judge import HeuristicJudge
from cqr.loader import GuidelineIndex
from cqr.schema import SCHEMA_VERSION, Transcript, Turn, reference_version
from cqr.staleness import is_stale, load_guidelines
from cqr.store import Store


def _t(id_: str, *, intent=None, reference=None) -> Transcript:
    return Transcript(id=id_, source="test", intent=intent, reference=reference,
                      turns=[Turn(idx=0, speaker="agent", text="hi"),
                             Turn(idx=1, speaker="customer", text="?")])


# ------------------------------------------------------------- is_stale ----

class TestIsStalePure:
    def test_matching_versions_not_stale(self):
        t = _t("a", reference="pol")
        r = HeuristicJudge().judge(t)
        assert not is_stale(r)

    def test_rubric_version_mismatch(self, monkeypatch):
        t = _t("a")
        r = HeuristicJudge().judge(t)
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        assert is_stale(r)

    def test_schema_version_mismatch(self, monkeypatch):
        t = _t("a")
        r = HeuristicJudge().judge(t)
        monkeypatch.setattr(schema, "SCHEMA_VERSION", "9.9")
        assert is_stale(r)

    def test_id_resolved_review_stale_when_guideline_version_changes(self, guidelines_dict, monkeypatch, tmp_path):
        """Score a review via reference_id, mutate the guideline, is_stale
        should say True — because reference_resolution=='id' and the current
        index reports a different Reference.version for that id."""
        # Set up the live index to point at a mutable temp file.
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        monkeypatch.setenv("CQR_GUIDELINES", str(p))
        from cqr.loader import _reset_index_for_tests
        _reset_index_for_tests()
        # Score with reference_id (uses the initial index).
        t = _t("id-review", intent=None)
        t.reference_id = "product_defect/refund_status"
        r = HeuristicJudge().judge(t)
        assert r.reference_resolution == "id"
        assert not is_stale(r)  # nothing has changed yet
        # Mutate the guideline text -> new version.
        new_dict = json.loads(json.dumps(guidelines_dict))
        new_dict["Product Defect"]["subflows"]["Refund Status"]["actions"][0]["text"] = "MUTATED"
        p.write_text(json.dumps(new_dict))
        _reset_index_for_tests()
        assert is_stale(r)

    def test_intent_resolved_review_stale_similarly(self, guidelines_dict, monkeypatch, tmp_path):
        """Same, but resolution came from intent — still uses reference_id
        set at _finalize time to check current version."""
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        monkeypatch.setenv("CQR_GUIDELINES", str(p))
        from cqr.loader import _reset_index_for_tests
        _reset_index_for_tests()
        t = _t("intent-review", intent="product_defect/refund_status")
        r = HeuristicJudge().judge(t)
        assert r.reference_resolution == "intent"
        assert not is_stale(r)
        new_dict = json.loads(json.dumps(guidelines_dict))
        new_dict["Product Defect"]["subflows"]["Refund Status"]["actions"][0]["text"] = "MUTATED"
        p.write_text(json.dumps(new_dict))
        _reset_index_for_tests()
        assert is_stale(r)

    def test_inline_only_reference_never_stale_via_guideline_path(self, guidelines_dict, monkeypatch, tmp_path):
        """Inline references stay never-stale — the stored transcript
        already carries them, so what was scored is what still exists."""
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        monkeypatch.setenv("CQR_GUIDELINES", str(p))
        from cqr.loader import _reset_index_for_tests
        _reset_index_for_tests()
        t = _t("a", reference="inline policy text", intent=None)
        r = HeuristicJudge().judge(t)
        assert r.reference_resolution == "inline"
        # Even after mutating every guideline in the index:
        p.write_text(json.dumps({"Empty Flow": {"subflows": {}}}))
        _reset_index_for_tests()
        assert not is_stale(r)

    def test_no_reference_id_no_reference_check(self, monkeypatch, tmp_path):
        """A resolution=='none' review has no reference_id to check — the
        reference-based check must NOT fire."""
        from cqr.loader import _reset_index_for_tests
        _reset_index_for_tests()
        t = _t("a", intent=None)  # no reference, no id, no intent
        r = HeuristicJudge().judge(t)
        assert r.reference_resolution == "none"
        assert not is_stale(r)


class TestLoadGuidelines:
    def test_returns_index_when_file_present(self, tmp_path, monkeypatch, guidelines_dict):
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        monkeypatch.setenv("CQR_GUIDELINES_PATH", str(p))
        gi = load_guidelines()
        assert gi is not None
        assert gi.reference_for("product_defect", "refund_status") is not None

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CQR_GUIDELINES_PATH", str(tmp_path / "nope.json"))
        assert load_guidelines() is None

    def test_loads_kb_when_present(self, tmp_path, monkeypatch, guidelines_dict):
        p = tmp_path / "guidelines.json"
        p.write_text(json.dumps(guidelines_dict))
        (tmp_path / "kb.json").write_text(json.dumps({"refund_status": ["a"]}))
        monkeypatch.setenv("CQR_GUIDELINES_PATH", str(p))
        gi = load_guidelines()
        assert gi.valid_slugs == {"refund_status"}


# -------------------------------------------- GET /reviews stale filter ----

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_store", Store(tmp_path / "reviews.json"))
    monkeypatch.setenv("CQR_JUDGE", "heuristic")
    monkeypatch.setattr(api, "_judge", HeuristicJudge())
    with TestClient(api.app) as c:
        yield c


def _post_review(client, id_):
    return client.post("/review", json={
        "id": id_, "source": "upload",
        "turns": [{"idx": 0, "speaker": "agent", "text": "hi"},
                  {"idx": 1, "speaker": "customer", "text": "?"}],
    })


class TestReviewsStaleFilter:
    def test_all_stale_when_rubric_bumped(self, api_client, monkeypatch):
        _post_review(api_client, "a")
        _post_review(api_client, "b")
        # Bump the rubric version everywhere it's read from.
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        stale = api_client.get("/reviews?stale=true").json()
        assert len(stale) == 2
        assert all(r["stale"] is True for r in stale)
        fresh = api_client.get("/reviews?stale=false").json()
        assert fresh == []

    def test_stale_flag_not_persisted_true(self, api_client, monkeypatch):
        _post_review(api_client, "a")
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        api_client.get("/reviews?stale=true")  # trigger the read
        # Direct store peek — still False on disk.
        assert api._store.get("a")[1].stale is False


class TestHealthStaleCount:
    def test_health_reports_stale_count(self, api_client, monkeypatch):
        _post_review(api_client, "a")
        _post_review(api_client, "b")
        assert api_client.get("/health").json()["stale_count"] == 0
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        body = api_client.get("/health").json()
        assert body["stale_count"] == 2
        assert body["reviews"] == 2


# ------------------------------------------------- cqr show stale count ----

class TestShowStale:
    def _seed(self, tmp_path):
        from cqr.loader import dump_jsonl
        t = _t("show-a")
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([t], jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        return out

    def test_show_header_prints_stale_count(self, tmp_path, monkeypatch, capsys):
        out = self._seed(tmp_path)
        capsys.readouterr()
        cli.main(["show", str(out)])
        assert "1 reviews, 0 stale" in capsys.readouterr().err

    def test_show_header_reflects_stale_after_bump(self, tmp_path, monkeypatch, capsys):
        out = self._seed(tmp_path)
        capsys.readouterr()
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        cli.main(["show", str(out)])
        err = capsys.readouterr().err
        assert "1 reviews, 1 stale" in err
        # And --json still emits `stale` on the record.
        cli.main(["show", str(out), "--json"])
        record = json.loads(capsys.readouterr().out)[0]
        assert record["stale"] is True


# --------------------------------------------------- cqr rereview ----------

class TestRereview:
    def _seed(self, tmp_path):
        from cqr.loader import dump_jsonl
        t1 = _t("rr-a")
        t2 = _t("rr-b")
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([t1, t2], jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        return out

    def test_rereview_no_op_when_nothing_stale(self, tmp_path, capsys):
        out = self._seed(tmp_path)
        capsys.readouterr()
        cli.main(["rereview", str(out), "--judge", "heuristic"])
        assert "nothing to rereview" in capsys.readouterr().err

    def test_rereview_clears_stale_flag(self, tmp_path, capsys, monkeypatch):
        out = self._seed(tmp_path)
        capsys.readouterr()
        # Bump the version — both reviews become stale.
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        cli.main(["rereview", str(out), "--stale", "--judge", "heuristic"])
        err = capsys.readouterr().err
        assert "rereview=2" in err
        assert "2 rescored" in err
        # After the rerun, no review is stale.
        cli.main(["show", str(out)])
        assert "0 stale" in capsys.readouterr().err

    def test_rereview_all(self, tmp_path, capsys):
        out = self._seed(tmp_path)
        capsys.readouterr()
        cli.main(["rereview", str(out), "--all", "--judge", "heuristic"])
        assert "rereview=2" in capsys.readouterr().err

    def test_rereview_judge_rejected_exits_3(self, tmp_path, capsys, monkeypatch):
        out = self._seed(tmp_path)
        capsys.readouterr()
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")

        from cqr.errors import JudgeRejected
        from cqr.judge import HeuristicJudge
        def boom(self, t):
            raise JudgeRejected("bad key", attempts=1)
        monkeypatch.setattr(HeuristicJudge, "judge", boom)
        with pytest.raises(SystemExit) as exc:
            cli.main(["rereview", str(out), "--stale", "--judge", "heuristic"])
        assert exc.value.code == 3

    def test_rereview_orphaned_review_counted_as_failure(self, tmp_path, capsys, monkeypatch):
        """A stored review whose transcript is missing (evicted, corrupted
        store) counts as a per-item failure but doesn't crash the rerun."""
        out = self._seed(tmp_path)
        capsys.readouterr()
        # Simulate an orphan: delete the transcript entry but keep the review.
        s = Store(out)
        del s._transcripts["rr-a"]
        s.flush()
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")
        with pytest.raises(SystemExit) as exc:
            cli.main(["rereview", str(out), "--stale", "--judge", "heuristic"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "transcript missing from store" in err

    def test_rereview_per_item_failures_exit_1(self, tmp_path, capsys, monkeypatch):
        out = self._seed(tmp_path)
        capsys.readouterr()
        monkeypatch.setattr(rubric, "RUBRIC_VERSION", "9.9")

        from cqr.judge import HeuristicJudge
        def boom(self, t):
            raise RuntimeError("planned")
        monkeypatch.setattr(HeuristicJudge, "judge", boom)
        with pytest.raises(SystemExit) as exc:
            cli.main(["rereview", str(out), "--stale", "--judge", "heuristic"])
        assert exc.value.code == 1
