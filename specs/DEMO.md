# Demo — 4:30 walkthrough

A minute-per-minute script for the prototype walkthrough. Read once before you demo. Each section: **click** (what appears on screen), **say** (one or two sentences, out loud), **notice** (what the audience should register).

Total budget: ~4:30, leaving 5+ minutes of the 10-minute Part 2 slot for tradeoffs commentary and audience questions.

---

## 0:00 — Context (~30 sec)

**Say**: "The brief asked: read a customer/agent transcript, decide what 'good' looks like, and give a supervisor something they can act on. I built a Conversation Quality Reviewer — five anchored signals per conversation, every judgment cites specific turns. What I'll show you is one command from clone to demo, then the three conversations that decided the signal set."

**Notice**: framing the brief before the terminal opens keeps the audience on the *product* problem, not the code.

---

## 0:30 — `scripts/demo.sh` (~30 sec)

**Click**: run the command in the terminal.

```bash
scripts/demo.sh
```

**Say**: "One command, no key needed. It copies the LLM eval to a scratch store so a demo run never dirties the committed evidence file — a habit I learned by shipping it wrong once."

**Notice**: the URLs that print. Dashboard, `/docs`, `/health`, three interesting review links. This is the surface a developer would find first.

---

## 1:00 — `syn-06-exemplary`, then `syn-01-wrong-but-happy` (~1:30, the payoff)

**Click**: open `http://127.0.0.1:8000/#/reviews/syn-06-exemplary` first.

**Say**: "This is the shape of a good conversation. Every card is green. Every judgment cites specific turns — click 'turn 4' and it highlights the turn in the transcript."

**Notice**: turn citations are the whole point. Score + rationale + evidence, or the signal isn't trustworthy.

Then click `http://127.0.0.1:8000/#/reviews/syn-01-wrong-but-happy`.

**Say**: "Now the same visual, but look — Resolution `resolved`, sentiment up, customer effort low. Customer literally says 'great service.' *And* correctness `contradicted`. Click the correctness turn — the reference is right there. Policy says wait if under 7 days. Customer said 3 days. Agent reshipped anyway. That's the failure mode every conventional CX metric misses: happy customer, wrong answer."

**Notice**: this is the moment. LLM judge scores it 22/22 on the synthetic set; the regex heuristic misses this case entirely (18/22).

---

## 2:30 — `syn-05-pii` + needs-review toggle (~45 sec)

**Click**: `http://127.0.0.1:8000/#/reviews/syn-05-pii`.

**Say**: "One flag — PII mishandling — puts the whole conversation in the human queue regardless of the other signals. Risk flags are a gate, not a score, deliberately never averaged into an overall score."

Go back to the dashboard root. Click the **"needs human review only"** checkbox.

**Say**: "Toggle the filter — the list collapses to the four conversations a supervisor needs to see now. That's what risk-flags-as-a-gate means in practice."

**Notice**: the checkbox rebuilds a supervisor queue in one click. That's the ops surface, not a nice-to-have.

---

## 3:15 — Batch flow + `/docs` (~45 sec)

**Click**: scroll to the terminal — the demo already ran the batch. Point at the block:

```
==> POST /review/batch?wait=true  (judge=heuristic)  <-  tests/fixtures/batch3.json
   job_id     = <uuid>
   status     = completed  (3/3)
```

**Say**: "The batch endpoint is async: 202 with a job id, workers score in a bounded queue, callers poll `/jobs/{id}` or hit `?wait=true` to block. `Idempotency-Key` header dedupes replays. Per-job circuit breaker so a broken provider doesn't burn budget on 200 doomed items."

Click `http://127.0.0.1:8000/docs`. Scroll to `POST /review`, expand the 503 response.

**Say**: "Every non-2xx the API can emit is a documented part of the contract — nine status codes across all routes. Look at 503 — `Retry-After: 30` header is in the spec, `ErrorBody` schema is linked. A test runs a middleware over the whole test session and fails if the app emits any status not declared here, so the spec can't silently drift from the code."

**Notice**: this is what a good SDK contract looks like. Retry logic is the first thing a developer writes against an API — it has to be truthful.

---

## 4:00 — Production boundary (~30 sec, face the panel)

**Say**: "About half a day, built with Claude Code. What I chose *not* to build is in TRADEOFFS.md — no database, no broker, no out-of-process workers, no PII redaction, no ensemble judge. Each one has a one-line reason. Judge accuracy is measured only against nine hand-labeled cases — that's a regression seed, not a benchmark. Reference lookup assumes a known intent, which is ABCD's assumption; production would need intent classification and KB retrieval. Those are the hardening items, not gaps I noticed after the fact."

**Notice**: closing on the boundary is the move — it shows you know exactly what you shipped and what you didn't.

---

## Skip beats if you're running long

- **Skip 3:15's `/docs` visit** — describe the contract in one sentence instead. Do NOT skip the batch/terminal beat.
- **Skip the needs-review toggle at 2:30** — describe it instead.
- **Do not skip 1:00.** That's the whole demo.
