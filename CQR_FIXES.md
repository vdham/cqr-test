# CQR fix list — instructions for Claude Code

Repo: `cqr-test` (Conversation Quality Reviewer). Run this **before** the separate "async batch queue + LiteLLM judge" prompt; that one assumes these are done.

Read `README.md`, `TRADEOFFS.md`, `cqr/schema.py`, `cqr/judge.py`, `cqr/loader.py`, `cqr/rubric.py`, `cqr/api.py`, `cqr/cli.py`, `cqr/static/index.html`, `scripts/eval_synthetic.py`, and `tests/conftest.py` first. Keep the architecture exactly as it is: `Transcript` in → `Review` out is the public contract; `_finalize()` in `judge.py` and the `@model_validator` on `Review` are the invariant layer; judges implement the `Judge` protocol. Tests must stay hermetic (no network, no API key) and coverage must stay at 100% of `cqr/*`.

Work through the sections in order. One commit per section, message prefixed as shown. Run the full acceptance checklist at the end.

Do **not**: add a database, a frontend framework, a queue, a second model, or new signals; change the five signals, the tiers, or the one-call-per-conversation design; touch retry logic or `temperature` handling in `AnthropicJudge` (a later task replaces that judge).

---

## Section A — Contract enforcement  (`fix(schema): …`)

### A1. Type-enforce signal levels
**Problem.** `SignalResult.level` is a bare `str`. `_finalize()` accepts `{"correctness": {"level": "CONTRADICTED"}}`, and `needs_human_review` then evaluates `False` because the validator compares with `==` against `"contradicted"`. `"Resolved."`, `"very high"`, `"ok"` are all accepted too. README claims derived fields are "computed in code, never trusted from the model"; that is currently false. A bad level also does not trigger the judge's retry loop.

**Fix.** In `cqr/schema.py`, make `SignalResult` generic over the level enum, or define four concrete result models:
```python
class ResolutionResult(SignalResult):   level: Resolution
class CorrectnessResult(SignalResult):  level: Correctness
class Level3Result(SignalResult):       level: Level3
```
and type `Review.resolution: ResolutionResult`, `correctness: CorrectnessResult`, `customer_effort: Level3Result`, `interaction_quality: Level3Result`. In `_finalize()`, before validation, normalize every `level` string with `.strip().lower()` so `"Resolved"` heals rather than retrying; anything else must raise `ValidationError` so the judge's retry fires. Update `Review._enforce_invariants` to compare enums, not strings. Update `HeuristicJudge` and any tests that build reviews.

**Tests.** `"CONTRADICTED"` → normalized → `needs_human_review is True`. `"very high"` → `ValidationError`. Existing 126 tests still pass.

### A2. Validate evidence turn citations
**Problem.** `turns=[99]` on a one-turn transcript, and a `sentiment_trajectory` point for turn 42, are accepted. Sentiment points can also cite agent turns. The model can invent its evidence.

**Fix.** In `_finalize()` (the transcript is in scope there): drop any index in any `turns` list (all four signals and every risk flag) that is not in `{t.idx for t in transcript.turns}`; drop any `sentiment_trajectory` entry whose `turn` is not a **customer** turn. Record what was dropped in a `Review.warnings: list[str]` field (new, default empty, e.g. `"resolution: dropped unknown turn 99"`). Don't reject the review for this — strip and warn.

**Tests.** Out-of-range and agent-turn citations are stripped, warning present, `sentiment_delta` recomputed from the surviving points.

### A3. Add `rubric_version`
**Fix.** `RUBRIC_VERSION = "1.0"` in `cqr/rubric.py`; `rubric_version: str` on `Review`; set in `_finalize()`. Show it in the dashboard meta line next to `judge`.

### A4. Tighten `Transcript` input validation
**Problem.** `POST /review` accepts `turns: []` (returns 200 and a review), duplicate `idx` values, and non-contiguous `idx`. The whole evidence story keys on `idx`.

**Fix.** On `Transcript`: `turns` min length 1; validator that `idx` values are unique and equal to their list position (or, if all `idx` are absent, assign them). Keep `source` a free string.

**Tests.** Empty turns → 422; duplicate idx → 422; contiguous idx → 200.

---

## Section B — Guideline lookup  (`fix(loader): …`)

### B1. `refund_status` resolves to the wrong guideline
**Problem.** `_norm()` stop-lists `status`, so `product_defect/refund_status` normalizes to `{refund}`, ties between "Initiate Refund" and "Refund Status", and first-wins returns **Initiate Refund**. `abcd-9489` in the shipped sample is judged against the wrong policy, which produces a false `contradicted` and a false human-review entry.

**Fix.** In `GuidelineIndex._find_subflow`: (1) try an exact match first — convert the snake_case key to Title Case (`refund_status` → `Refund Status`) and compare to subflow names within the flow; (2) then `ALIASES`; (3) only then token overlap. Add `"refund_status": "Refund Status"` and `"refund_update": "Update Refund"` to `ALIASES`. Remove `status` and `manage` from the stop list — they are the discriminating tokens in Order Issue, Manage Account, and Subscription Inquiry. Validate the incoming subflow key against the keys of `data/abcd/kb.json` (they are the exact set of 55 valid slugs) and log a warning on an unknown slug instead of fuzzy-matching silently.

**Tests.** A parametrized test over **every** `(flow, subflow)` pair in `data/abcd/kb.json` asserting the resolved subflow title; a specific test that `refund_status` → `Refund Status`.

### B2. Wire `kb.json` in, or delete it — decision: use it for B1's validation only
`kb.json` is now read for slug validation (B1). Do not build a procedure-adherence check. Add one sentence to TRADEOFFS §Harden describing that check as future work (expected action sequence from `kb.json` vs observed action slugs in ABCD's `delexed[].targets`, restricted to unconditional verification steps → deterministic `missing_disclosure`).

---

## Section C — Rubric  (`fix(rubric): …`)

### C1. Scope correctness; align the no-reference rule
**Problem.** ABCD guidelines are mostly internal tool steps (`[Pull up Account]`, `[Validate Purchase]`). ABCD `action` turns do reach the judge as `system` text, but not every step leaves a trace, so the judge can mark `contradicted` for a step it cannot see. Separately, `build_user_prompt` says no-reference correctness is `unverifiable` "unless the agent contradicts something universally known", while `_finalize` forces `unverifiable` unconditionally.

**Fix.** In `rubric.py` §3 add: *"Judge customer-facing claims, outcomes and observable actions (including SYSTEM action turns). Do not penalize a missing internal tool step unless the transcript shows it was skipped."* Remove the "universally known" clause from `build_user_prompt` so the prompt and the invariant say the same thing. Bump `RUBRIC_VERSION` to `"1.1"`.

---

## Section D — Eval, dashboard, hygiene  (`fix(misc): …`)

### D1. Eval parser reads only the first expected value
`syn-02`'s label is `correctness=unverifiable or contradicted`; the regex captures `unverifiable` only. Change the label in `data/synthetic.jsonl` (and `scripts/make_synthetic.py`) to `correctness=unverifiable|contradicted`, and make `scripts/eval_synthetic.py` treat `|` as OR (`got in want.split("|")`). Also let it read `--store PATH` and print the judge name and `rubric_version` in the header.

### D2. Dashboard escapes only some strings
`esc()` is applied to turn text and the reference only; `transcript_id`, `summary`, every `rationale`, `intent`, and `judge` go into `innerHTML` raw. Wrap all of them in `esc()`.

### D3. Heuristic regexes are fitted to the synthetic transcripts
No code change. Add to TRADEOFFS §Corners cut: *"The heuristic patterns were written after the synthetic transcripts; its eval score shows the pipeline works, not that regex is a calibrated baseline."*

### D4. CLI `show`
Add `--needs-review`, `--source NAME`, and `--json` (emit the sorted list as JSON to stdout). Move `_sort_key` from `api.py` into `schema.py` (or a small `cqr/ordering.py`) and use it in both `api.py` and `cli.py` so the CLI and API order identically (today `cmd_show` lacks the `unresolved` tiebreak). Align `--limit` default with the README example. Document that `review` merges into `--out` by transcript id across runs, and add `--fresh` to start with an empty store.

### D5. README Python-usage snippet
Under §Contract add a four-line "callable function" example:
```python
from cqr.judge import get_judge
from cqr.schema import Transcript
review = get_judge().judge(Transcript(id="c1", source="upload", turns=[...]))
review.model_dump()
```

### D6. OpenAPI: defaulted fields look optional
`Review.risk_flags`, `needs_human_review`, `sentiment_delta` have defaults, so the generated spec marks them optional. Either drop the defaults (the validator always sets them) or state "always present" in each field's description. Prefer dropping the defaults for `needs_human_review` and `sentiment_delta`.

---

## Section E — Docs  (`docs: …`)

### E1. Fix contradictions
- `TRADEOFFS.md` §Corners cut: replace *"No tests beyond the synthetic eval script"* with *"Unit and API paths are covered by hermetic tests (100% line coverage); model-behaviour evaluation is limited to nine hand-labeled synthetic conversations."*
- `README.md` §Tests: `eval_synthetic.py` does **not** call the model; it scores stored reviews against `metadata.expect`. Say "run `review` first".
- `README.md` §Run: "first 15 ABCD dev conversations" → "the 3 shipped ABCD samples (up to 15 with the full dataset in `data/abcd/`)".
- Document `POST /review` idempotency: re-posting an existing `id` replaces the stored transcript and review.
- Note the batch size / synchronous behaviour of `POST /review/batch` as a current limitation (a later task replaces it).

### E2. Remove `docs/jev-analysis.md`; relabel `docs/PLAN.md`
Delete `docs/jev-analysis.md`. Rename `docs/PLAN.md` → `docs/ORIGINAL_PLAN.md` and prepend:
> **Initial design (superseded).** What changed between this plan and the implementation, and why: dropped the composite `overall` score (a risk gate must not be averaged away); dropped *escalation likelihood* (derivable from unresolved + negative sentiment delta + flags); replaced *empathy* with behaviour-anchored *interaction quality*; demoted sentiment to context; added *correctness* against a reference as the signal that catches "happy customer, wrong answer"; one LLM call per conversation instead of five (shared context, cost) with correctness split out as the first thing to separate in production. See `TRADEOFFS.md`.

### E3. Commit evidence of the LLM judge (only if `ANTHROPIC_API_KEY` is available in the environment; otherwise skip and leave a TODO in README)
Run `python -m cqr.cli review --synthetic data/synthetic.jsonl --judge anthropic --out examples/reviews.anthropic.json` and `python scripts/eval_synthetic.py --store examples/reviews.anthropic.json`. Commit `examples/reviews.anthropic.json` (synthetic only — no ABCD text). Add a small table to README §Tests: judge · model · rubric version · eval score, with both the heuristic and the Anthropic rows. Note in README that `CQR_STORE=examples/reviews.anthropic.json uvicorn cqr.api:app` runs the dashboard on the committed reviews with no key.

### E4. TRADEOFFS additions (one or two sentences each, under §Harden for production unless noted)
- Layered risk detection: deterministic PII/keyword detectors unioned with the model's flags; the risk path needs recall.
- Untrusted content at two boundaries: prompt-injection resistance at the judge, output encoding at the UI. Redaction with **typed placeholders** (`<CARD_NUMBER>`, `<EMAIL>`, `<NAME_1>`) rather than deletion, so `pii_mishandling` and "used the customer's specifics" still work; reversal table stored separately.
- Under §Risk flags: `needs_human_review` is an urgent exception queue, not the QA sampling queue — `unresolved` + high effort + low quality with no flag and supported correctness does not route to it, by design.
- ABCD structured action annotations (`delexed[].targets`) and `kb.json` sequences are unused beyond slug validation; they would ground `resolution` and the procedural half of `correctness` deterministically.

---

## Acceptance checklist
1. `pytest` green; `coverage run --source=cqr -m pytest && coverage report` → 100% for every `cqr/*` file.
2. This snippet raises `ValidationError` for `"very high"`, and with `"very high"` replaced by `"high"` prints `resolved contradicted [] True` with a warning about turn 99:
   ```python
   from cqr.schema import *; from cqr.judge import _finalize
   t = Transcript(id="x", source="upload", reference="policy", turns=[Turn(idx=0, speaker="customer", text="hi")])
   raw = {"resolution": {"level": "Resolved.", "rationale": "", "turns": [99]},
          "correctness": {"level": "CONTRADICTED", "rationale": "", "turns": []},
          "customer_effort": {"level": "very high", "rationale": "", "turns": []},
          "interaction_quality": {"level": "ok", "rationale": "", "turns": []},
          "sentiment_trajectory": [{"turn": 0, "score": 0.2}, {"turn": 42, "score": 0.9}], "summary": "s"}
   r = _finalize(t, raw, "test"); print(r.resolution.level, r.correctness.level, r.resolution.turns, r.needs_human_review, r.warnings)
   ```
3. `python -c "import json; from cqr.loader import GuidelineIndex; gi=GuidelineIndex(json.load(open('data/abcd/guidelines.json'))); print(gi._find_subflow('product_defect','refund_status')[1])"` → `Refund Status`.
4. `python -m cqr.cli review --synthetic data/synthetic.jsonl --abcd data/abcd --judge heuristic && python scripts/eval_synthetic.py` → 18/22 (the heuristic number is unchanged by these fixes).
5. `python -m cqr.cli show --needs-review --json | python -m json.tool` → valid JSON, 4 entries on the heuristic run.
6. `uvicorn cqr.api:app` → `POST /review` with `turns: []` → 422; dashboard loads; clicking a turn citation highlights the turn; meta line shows `rubric_version`.
7. `git status` shows `docs/jev-analysis.md` deleted and `docs/ORIGINAL_PLAN.md` present; `grep -n "No tests beyond" TRADEOFFS.md` returns nothing; `grep -n "universally known" cqr/rubric.py` returns nothing.
8. `git diff --stat` touches only `cqr/*`, `tests/*`, `scripts/*`, `data/synthetic.jsonl`, `docs/*`, `examples/*` (if E3 ran), `README.md`, `TRADEOFFS.md`.
