"""CLI: main dispatch for `review` and `show` commands."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cqr import cli
from cqr.loader import dump_jsonl


class TestReviewCommand:
    def test_synthetic_input_produces_reviews(self, tmp_path, sample_transcript, capsys):
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        assert out.exists()
        data = json.loads(out.read_text())
        assert sample_transcript.id in data["reviews"]
        captured = capsys.readouterr()
        assert "judge=heuristic" in captured.err
        assert "0 failed" in captured.err

    def test_jsonl_input_alias(self, tmp_path, sample_transcript):
        jsonl = tmp_path / "in.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], jsonl)
        cli.main(["review", "--jsonl", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        assert json.loads(out.read_text())["reviews"]

    def test_abcd_input(self, tmp_path, abcd_convo_dict, guidelines_dict):
        data_dir = tmp_path / "abcd"
        data_dir.mkdir()
        (data_dir / "guidelines.json").write_text(json.dumps(guidelines_dict))
        (data_dir / "abcd_sample.json").write_text(json.dumps([abcd_convo_dict]))
        out = tmp_path / "reviews.json"
        cli.main(["review", "--abcd", str(data_dir), "--judge", "heuristic",
                  "--out", str(out)])
        data = json.loads(out.read_text())
        assert "abcd-999" in data["reviews"]

    def test_no_input_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            cli.main(["review", "--judge", "heuristic", "--out", str(tmp_path / "r.json")])

    def test_periodic_flush_every_5(self, tmp_path, make_turn, capsys):
        """CLI flushes every 5 items to keep the store on disk mid-batch."""
        from cqr.schema import Transcript
        transcripts = [
            Transcript(id=f"conv-{i}", source="test",
                       turns=[make_turn(0, "agent", "hi"),
                              make_turn(1, "customer", "help")])
            for i in range(7)  # >5 to trigger the periodic flush at i==5
        ]
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl(transcripts, jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        data = json.loads(out.read_text())
        assert len(data["reviews"]) == 7

    def test_judge_failure_counted_not_raised(self, tmp_path, sample_transcript,
                                              capsys, monkeypatch):
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], jsonl)

        from cqr.judge import HeuristicJudge
        def boom(self, t): raise RuntimeError("planned")
        monkeypatch.setattr(HeuristicJudge, "judge", boom)

        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        err = capsys.readouterr().err
        assert "1 failed" in err
        assert "RuntimeError" in err


class TestShowCommand:
    def _seed(self, tmp_path, transcripts):
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl(transcripts, jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        return out

    def test_prints_stored_reviews(self, tmp_path, sample_transcript, capsys):
        out = self._seed(tmp_path, [sample_transcript])
        capsys.readouterr()
        cli.main(["show", str(out)])
        stdout = capsys.readouterr().out
        assert sample_transcript.id in stdout
        assert "risk=" in stdout
        assert "res=" in stdout

    def test_show_default_path(self, tmp_path, monkeypatch, sample_transcript, capsys):
        monkeypatch.chdir(tmp_path)
        jsonl = tmp_path / "syn.jsonl"
        dump_jsonl([sample_transcript], jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic"])
        capsys.readouterr()
        cli.main(["show"])
        assert sample_transcript.id in capsys.readouterr().out

    def test_needs_review_filter(self, tmp_path, capsys, make_turn):
        # Two convos: one with a PII trigger (needs review), one clean.
        from cqr.schema import Transcript
        clean = Transcript(id="clean", source="synthetic",
                           turns=[make_turn(0, "agent", "hi"),
                                  make_turn(1, "customer", "help")])
        pii = Transcript(id="pii-1", source="synthetic",
                         turns=[make_turn(0, "customer", "card update"),
                                make_turn(1, "agent", "share your full 16-digit card number and CVV")])
        out = self._seed(tmp_path, [clean, pii])
        capsys.readouterr()
        cli.main(["show", str(out), "--needs-review"])
        stdout = capsys.readouterr().out
        assert "pii-1" in stdout
        assert "clean" not in stdout

    def test_source_filter(self, tmp_path, capsys, make_turn):
        from cqr.schema import Transcript
        syn = Transcript(id="syn", source="synthetic",
                         turns=[make_turn(0, "agent", "hi"), make_turn(1, "customer", "?")])
        up = Transcript(id="up", source="upload",
                        turns=[make_turn(0, "agent", "hi"), make_turn(1, "customer", "?")])
        out = self._seed(tmp_path, [syn, up])
        capsys.readouterr()
        cli.main(["show", str(out), "--source", "synthetic"])
        stdout = capsys.readouterr().out
        assert "syn" in stdout and "up " not in stdout  # 'up' as bare id column

    def test_json_output_is_valid_json(self, tmp_path, capsys, sample_transcript):
        import json as _json
        out = self._seed(tmp_path, [sample_transcript])
        capsys.readouterr()
        cli.main(["show", str(out), "--json"])
        data = _json.loads(capsys.readouterr().out)
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["transcript_id"] == sample_transcript.id


class TestFreshFlag:
    def test_fresh_wipes_prior_store(self, tmp_path, sample_transcript, make_turn):
        from cqr.schema import Transcript
        first = tmp_path / "one.jsonl"
        second = tmp_path / "two.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], first)
        cli.main(["review", "--synthetic", str(first), "--judge", "heuristic",
                  "--out", str(out)])
        # Second run with --fresh discards the first review.
        other = Transcript(id="other", source="synthetic",
                           turns=[make_turn(0, "agent", "hi"), make_turn(1, "customer", "help")])
        dump_jsonl([other], second)
        cli.main(["review", "--synthetic", str(second), "--judge", "heuristic",
                  "--out", str(out), "--fresh"])
        from cqr.store import Store
        remaining = {r.transcript_id for r in Store(out).all()}
        assert remaining == {"other"}

    def test_default_merges_by_id(self, tmp_path, sample_transcript, make_turn):
        from cqr.schema import Transcript
        first = tmp_path / "one.jsonl"
        second = tmp_path / "two.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], first)
        cli.main(["review", "--synthetic", str(first), "--judge", "heuristic",
                  "--out", str(out)])
        other = Transcript(id="other", source="synthetic",
                           turns=[make_turn(0, "agent", "hi"), make_turn(1, "customer", "help")])
        dump_jsonl([other], second)
        cli.main(["review", "--synthetic", str(second), "--judge", "heuristic",
                  "--out", str(out)])
        from cqr.store import Store
        remaining = {r.transcript_id for r in Store(out).all()}
        assert remaining == {sample_transcript.id, "other"}


class TestArgparse:
    def test_no_subcommand_exits(self):
        with pytest.raises(SystemExit):
            cli.main([])

    def test_unknown_judge_choice_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            cli.main(["review", "--judge", "mystery"])
