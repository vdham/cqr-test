# Conversation Quality Reviewer

Reads customer–agent transcripts, scores each on five anchored quality signals with rationale and turn citations, and exposes the results as an API, a CLI, and a small dashboard.

Signals (see `TRADEOFFS.md` for why):

| Tier | Signal | Output |
|---|---|---|
| 1 · must know | **risk_flags** | list of `{type, severity, rationale, turns}` — a gate, not a score |
| 1 | **resolution** | `resolved` · `partially_resolved` · `deferred_with_owner` · `unresolved` |
| 1 | **correctness** | `supported` · `contradicted` · `unverifiable` (vs. a reference policy) |
| 2 · explain | **customer_effort** | `low` · `medium` · `high` (high is bad) |
| 2 | **interaction_quality** | `low` · `medium` · `high` |
| context | sentiment_trajectory | per customer turn, −1…+1, plus `sentiment_delta` |

Every signal returns `{level, rationale, turns}`. Derived fields (`needs_human_review`, `sentiment_delta`, risk-flag deduping, trajectory sort) are enforced by the `Review` schema at construction — see [Invariants](#invariants) below.

## Setup

```bash
git clone https://github.com/vdham/cqr-asapp && cd cqr-asapp
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...        # omit to use the offline regex baseline judge
```

Data: `data/abcd/` holds ABCD's `abcd_sample.json` (3 convos), `guidelines.json`, `kb.json`. For more ABCD conversations drop `abcd_v1.1.json.gz` from https://github.com/asappresearch/abcd into `data/abcd/` and the loader picks it up. `data/synthetic.jsonl` has nine deliberately problematic transcripts (regenerate with `python scripts/make_synthetic.py data/abcd`).

## Run

```bash
# batch review: synthetic set + first 15 ABCD dev conversations -> out/reviews.json
python -m cqr.cli review --synthetic data/synthetic.jsonl --abcd data/abcd --limit 15

# force the offline baseline (no API key needed)
python -m cqr.cli review --synthetic data/synthetic.jsonl --judge heuristic

# riskiest first
python -m cqr.cli show

# how did the judge do on the labeled synthetic set
python scripts/eval_synthetic.py

# API + dashboard
uvicorn cqr.api:app --reload      # dashboard http://127.0.0.1:8000/  ·  Swagger UI http://127.0.0.1:8000/docs (prefilled example bodies)
```

## Contract

Input is a `Transcript`; output is a `Review`. Both are Pydantic models in `cqr/schema.py`, and FastAPI publishes the OpenAPI spec at `/docs`.

```jsonc
// POST /review
{
  "id": "conv-123",
  "source": "upload",
  "intent": "shipping_issue/missing",          // optional
  "reference": "AGENT GUIDELINES: ...",        // optional; without it correctness = unverifiable
  "turns": [
    {"idx": 0, "speaker": "agent",    "text": "Hi, how can I help?"},
    {"idx": 1, "speaker": "customer", "text": "My package never came..."}
  ]
}
```

```jsonc
// -> Review
{
  "transcript_id": "conv-123",
  "risk_flags": [{"type": "unauthorized_promise", "severity": "high", "rationale": "...", "turns": [4]}],
  "resolution":          {"level": "resolved",     "rationale": "...", "turns": [4, 6]},
  "correctness":         {"level": "contradicted", "rationale": "...", "turns": [4]},
  "customer_effort":     {"level": "low",          "rationale": "...", "turns": []},
  "interaction_quality": {"level": "medium",       "rationale": "...", "turns": [2]},
  "sentiment_trajectory": [{"turn": 1, "score": -0.4}, {"turn": 7, "score": 0.8}],
  "sentiment_delta": 1.2,
  "needs_human_review": true,
  "summary": "Reshipped at 3 days against the 7-day rule; customer happy, policy violated.",
  "judge": "anthropic:claude-sonnet-4-5"
}
```

Other endpoints: `POST /review/batch` `{transcripts: [...]}`, `GET /reviews?needs_human_review=true`, `GET /reviews/{id}` (transcript + review).

### Invariants

Any `Review` — however it was constructed (judge output, API request, test fixture) — satisfies these guarantees, enforced by a `@model_validator` in `cqr/schema.py`:

- **`risk_flags`**: exactly one entry per `(type, severity)` pair. Duplicates collapse; turns aggregate and sort; rationales dedupe-and-join with `" | "`. Output is sorted by severity desc, then type asc.
- **`sentiment_trajectory`**: sorted by turn ascending.
- **`sentiment_delta`**: derived as `last.score − first.score` rounded to 2dp (`0.0` when fewer than two points).
- **`needs_human_review`**: `true` iff any risk flag is `medium` or `high`, OR `correctness == contradicted`.
- **`correctness`**: forced to `unverifiable` at judge time when the transcript has no `reference` (this one lives in `judge._finalize` since it depends on external context, not the review alone).

Callers should never compute the derived fields themselves — supplied values are overwritten.

### Error behavior

The Anthropic judge validates its own output against `Review` and retries up to twice on malformed JSON before raising `RuntimeError`. `POST /review/batch` collects per-transcript failures into an `errors` array rather than aborting the batch, so partial success is the norm; `reviews` and `errors` are both returned. `POST /review` (single) surfaces the error as a 500 with the exception type and message.

### What's stable

The `Transcript` and `Review` schemas are the public contract. Judge implementations, rubric wording, dashboard HTML, storage layout, and CLI flag names are internal and may change.

## Layout

```
cqr/schema.py      the contract (Transcript in, Review out)
cqr/rubric.py      the anchored rubric — this is the product
cqr/judge.py       AnthropicJudge (LLM) and HeuristicJudge (regex baseline); both emit Review
cqr/loader.py      ABCD + JSONL ingest; maps ABCD intents to guideline text for correctness
cqr/cli.py         batch runner
cqr/api.py         FastAPI endpoints + dashboard
cqr/static/        one-file dashboard
scripts/           synthetic data generator, synthetic eval
```
