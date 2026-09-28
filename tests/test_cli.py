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
    def test_prints_stored_reviews(self, tmp_path, sample_transcript, capsys):
        jsonl = tmp_path / "syn.jsonl"
        out = tmp_path / "reviews.json"
        dump_jsonl([sample_transcript], jsonl)
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic",
                  "--out", str(out)])
        capsys.readouterr()  # discard review output

        cli.main(["show", str(out)])
        stdout = capsys.readouterr().out
        assert sample_transcript.id in stdout
        assert "risk=" in stdout
        assert "res=" in stdout

    def test_show_default_path(self, tmp_path, monkeypatch, sample_transcript, capsys):
        monkeypatch.chdir(tmp_path)
        jsonl = tmp_path / "syn.jsonl"
        dump_jsonl([sample_transcript], jsonl)
        # write to the default location (out/reviews.json).
        cli.main(["review", "--synthetic", str(jsonl), "--judge", "heuristic"])
        capsys.readouterr()
        cli.main(["show"])  # default path
        assert sample_transcript.id in capsys.readouterr().out


class TestArgparse:
    def test_no_subcommand_exits(self):
        with pytest.raises(SystemExit):
            cli.main([])

    def test_unknown_judge_choice_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            cli.main(["review", "--judge", "mystery"])
