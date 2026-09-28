"""
CLI: batch-review transcripts.

  python -m cqr.cli review --abcd data/abcd --limit 15 --synthetic data/synthetic.jsonl
  python -m cqr.cli review --jsonl my_transcripts.jsonl --judge heuristic
  python -m cqr.cli show out/reviews.json
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .judge import get_judge
from .loader import load_abcd, load_jsonl
from .schema import Transcript
from .store import Store


def _collect(args) -> list[Transcript]:
    ts: list[Transcript] = []
    if args.synthetic:
        ts += load_jsonl(Path(args.synthetic))
    if args.jsonl:
        ts += load_jsonl(Path(args.jsonl))
    if args.abcd:
        ts += load_abcd(Path(args.abcd), split=args.split, limit=args.limit, offset=args.offset)
    return ts


def cmd_review(args):
    judge = get_judge(args.judge)
    store = Store(Path(args.out))
    ts = _collect(args)
    if not ts:
        sys.exit("no transcripts; pass --abcd, --synthetic, or --jsonl")
    print(f"judge={judge.name}  transcripts={len(ts)}  out={args.out}", file=sys.stderr)
    errors = 0
    t0 = time.time()
    for i, t in enumerate(ts, 1):
        try:
            r = judge.judge(t)
            store.put(t, r)
            flag = "!" if r.needs_human_review else " "
            print(f"{flag} {t.id:28s} res={r.resolution.level:22s} corr={r.correctness.level:13s} "
                  f"effort={r.customer_effort.level:6s} iq={r.interaction_quality.level:6s} "
                  f"flags={[f.type.value for f in r.risk_flags]}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"x {t.id}: {type(e).__name__}: {e}", file=sys.stderr)
        if i % 5 == 0:
            store.flush()
    store.flush()
    print(f"done: {len(ts) - errors} ok, {errors} failed, {time.time() - t0:.1f}s", file=sys.stderr)


def cmd_show(args):
    store = Store(Path(args.path))
    rs = sorted(store.all(), key=lambda r: (-r.max_risk, r.correctness.level != "contradicted", r.transcript_id))
    for r in rs:
        flag = "!" if r.needs_human_review else " "
        print(f"{flag} {r.transcript_id:28s} risk={r.max_risk} res={r.resolution.level:22s} corr={r.correctness.level:13s} | {r.summary}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="cqr")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="review a batch of transcripts")
    r.add_argument("--abcd", help="path to ABCD data dir (with guidelines.json)")
    r.add_argument("--split", default="dev")
    r.add_argument("--limit", type=int, default=10)
    r.add_argument("--offset", type=int, default=0)
    r.add_argument("--synthetic", help="path to synthetic.jsonl")
    r.add_argument("--jsonl", help="any JSONL of Transcript objects")
    r.add_argument("--judge", choices=["anthropic", "heuristic"], default=None,
                   help="default: anthropic if ANTHROPIC_API_KEY set, else heuristic")
    r.add_argument("--out", default="out/reviews.json")
    r.set_defaults(fn=cmd_review)

    s = sub.add_parser("show", help="print a stored review set, riskiest first")
    s.add_argument("path", nargs="?", default="out/reviews.json")
    s.set_defaults(fn=cmd_show)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    main()
