# AGENTS.md — how to work in this repo

Conversation Quality Reviewer (CQR): scores customer–agent transcripts on five anchored signals and exposes them as API, CLI and dashboard. Read `README.md` (contract), `TRADEOFFS.md` (why), then `STATUS.md` (where we are).

## Run / test
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                                  # hermetic, no network, must stay green
coverage run --source=cqr -m pytest && coverage report  # must stay 100% for cqr/*
python -m cqr.cli review --synthetic data/synthetic.jsonl --abcd data/abcd --judge heuristic
python scripts/eval_synthetic.py
uvicorn cqr.api:app --reload                            # http://127.0.0.1:8000/  ·  /docs
```
No `ANTHROPIC_API_KEY` → heuristic judge. Never commit `out/` or any file containing a key.

## Where things are
- `cqr/schema.py` — the public contract (`Transcript` in, `Review` out). Invariants live in `Review`'s model validator and `judge._finalize`.
- `cqr/rubric.py` — the rubric. Any wording change bumps `RUBRIC_VERSION`.
- `cqr/judge.py` — judges implement the `Judge` protocol; all must return a `Review`.
- `cqr/loader.py` — ABCD + JSONL ingest; guideline lookup.
- `specs/<NNN-name>/` — one folder per unit of work: `spec.md` (what/why), `tasks.md` (ordered checklist, ticked per commit).
- `docs/decisions/` — ADRs for choices the panel will ask about.

## Working agreement
- Pick the lowest-numbered `specs/*/tasks.md` with unticked items; work top to bottom; tick the box in the same commit as the change.
- One commit per task group, message prefixed `fix(scope):`, `feat(scope):`, `docs:`, `test:`.
- Update `STATUS.md` at the end of every session (three lines: done, next, blocked).
- `git status` is the last command of a session, run *after* any demo or script that could modify state — "nothing to commit" only means something if you check it after the last mutating action.
- Do not: add a database, queue broker, frontend framework, second provider, or new signals; change the five signals, the tiers, or the one-call design; loosen an invariant to make a test pass.
- Tests stay hermetic: stub the model client; never call a provider in `tests/`.
