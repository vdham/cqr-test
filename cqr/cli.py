"""
CLI: batch-review transcripts.

  python -m cqr.cli review --abcd data/abcd --limit 15 --synthetic data/synthetic.jsonl
  python -m cqr.cli review --jsonl my_transcripts.jsonl --judge heuristic --fresh
  python -m cqr.cli review --jsonl big.jsonl --judge anthropic --concurrency 4
  python -m cqr.cli show --needs-review --source synthetic
  python -m cqr.cli show --json | jq .

`review` merges into `--out` by transcript id: successive runs update matching
ids and add new ones. Pass `--fresh` to start from an empty store instead.

Exit codes:
  0 = all reviewed successfully
  1 = at least one per-transcript failure
  2 = an input file / dir doesn't exist
  3 = JudgeRejected (bad key, wrong model, permission denied) — stop immediately
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional

from .errors import JudgeRejected
from .judge import get_judge
from .loader import load_abcd, load_jsonl
from .rubric import RUBRIC_VERSION
from .schema import Review, Transcript, reference_version, sort_key
from .staleness import is_stale, load_guidelines
from .store import Store


def _load_jsonl_lenient(path: Path, label: str) -> list[Transcript]:
    """Skip lines that don't parse as a Transcript, print a count to stderr."""
    good: list[Transcript] = []
    bad = 0
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            good.append(Transcript.model_validate_json(line))
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  skipping {label}:{lineno}: {type(e).__name__}", file=sys.stderr)
    if bad:
        print(f"{bad} malformed line(s) skipped from {path}", file=sys.stderr)
    return good


def _collect(args) -> list[Transcript]:
    ts: list[Transcript] = []
    for path_arg, kind in ((args.synthetic, "synthetic"), (args.jsonl, "jsonl")):
        if not path_arg:
            continue
        p = Path(path_arg)
        if not p.exists():
            print(f"error: {kind} file not found: {p}", file=sys.stderr)
            sys.exit(2)
        ts += _load_jsonl_lenient(p, kind)
    if args.abcd:
        try:
            ts += load_abcd(Path(args.abcd), split=args.split, limit=args.limit, offset=args.offset)
        except FileNotFoundError as e:
            print(f"error: {e}", file=sys.stderr)
            sys.exit(2)
    return ts


def _run_all(judge, ts: list[Transcript], concurrency: int, store: Store | None = None,
             force: bool = False):
    """Yield (transcript, review_or_None, exception_or_None, cache_hit) as
    work completes. When `store` is provided and `force=False`, transcripts
    with a content-cache match are yielded from the store without calling
    the judge — the returned review carries `cache_hit=True`."""

    def _handle(t: Transcript):
        if store is not None and not force:
            hit = store.find(t.digest(), RUBRIC_VERSION,
                              reference_version(t.reference), judge.name)
            if hit is not None:
                return t, hit.model_copy(update={"cache_hit": True}), None, True
        try:
            return t, judge.judge(t), None, False
        except Exception as e:  # noqa: BLE001
            return t, None, e, False

    if concurrency <= 1:
        for t in ts:
            yield _handle(t)
        return
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(_handle, t): t for t in ts}
        for future in as_completed(futures):
            yield future.result()


def cmd_review(args) -> int:
    judge = get_judge(args.judge)
    out_path = Path(args.out)
    if args.fresh and out_path.exists():
        out_path.unlink()
    store = Store(out_path)
    ts = _collect(args)
    if not ts:
        sys.exit("no transcripts; pass --abcd, --synthetic, or --jsonl")
    # Intent-sort so consecutive judge calls share the cacheable reference
    # prefix on providers that support prompt caching. Ordering never affects
    # results — each review is independent.
    ts = sorted(ts, key=lambda t: (t.intent or "", t.id))
    print(f"judge={judge.name}  transcripts={len(ts)}  concurrency={args.concurrency}  out={args.out}", file=sys.stderr)
    errors = 0
    cached = 0
    t0 = time.time()
    for i, (t, r, err, cache_hit) in enumerate(
        _run_all(judge, ts, args.concurrency, store=store, force=args.force), 1
    ):
        if isinstance(err, JudgeRejected):
            # Config-scope failure (bad key etc.): further items would fail
            # identically. Bail out immediately with a dedicated exit code.
            store.flush()
            print(f"x {t.id}: JudgeRejected: {err}", file=sys.stderr)
            print("stopping: judge rejected the request (config-scope). Fix credentials or model and retry.", file=sys.stderr)
            sys.exit(3)
        if err is not None:
            errors += 1
            print(f"x {t.id}: {type(err).__name__}: {err}", file=sys.stderr)
        else:
            if not cache_hit:
                store.put(t, r)
            else:
                cached += 1
            flag = "!" if r.needs_human_review else " "
            marker = "c" if cache_hit else flag
            print(f"{marker} {t.id:28s} res={r.resolution.level.value:22s} corr={r.correctness.level.value:13s} "
                  f"effort={r.customer_effort.level.value:6s} iq={r.interaction_quality.level.value:6s} "
                  f"flags={[f.type.value for f in r.risk_flags]}", file=sys.stderr)
        if i % 5 == 0:
            store.flush()
    store.flush()
    print(f"done: {len(ts) - errors - cached} scored, {cached} cached, {errors} failed, {time.time() - t0:.1f}s", file=sys.stderr)
    if errors:
        sys.exit(1)
    return 0


def _annotate_stale_list(store: Store, reviews: list[Review]) -> list[Review]:
    gi = load_guidelines()
    out: list[Review] = []
    for r in reviews:
        hit = store.get(r.transcript_id)
        transcript = hit[0] if hit else None
        if is_stale(r, transcript=transcript, guidelines=gi):
            out.append(r.model_copy(update={"stale": True}))
        else:
            out.append(r)
    return out


def cmd_show(args) -> int:
    store = Store(Path(args.path))
    rs = store.all()
    if args.needs_review:
        rs = [r for r in rs if r.needs_human_review]
    if args.source:
        rs = [r for r in rs if r.source == args.source]
    rs = _annotate_stale_list(store, rs)
    rs = sorted(rs, key=sort_key)
    stale_count = sum(1 for r in rs if r.stale)

    if args.json:
        json.dump([r.model_dump(mode="json") for r in rs], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"{len(rs)} reviews, {stale_count} stale", file=sys.stderr)
    for r in rs:
        flag = "!" if r.needs_human_review else " "
        stale_marker = " (stale)" if r.stale else ""
        print(f"{flag} {r.transcript_id:28s} risk={r.max_risk} res={r.resolution.level.value:22s} corr={r.correctness.level.value:13s}{stale_marker} | {r.summary}")
    return 0


def cmd_rereview(args) -> int:
    """Re-run the judge for stale reviews (or all with --all), bypassing the
    content cache. Replaces stored reviews in place; the store is flushed at
    the end. Same exit-code contract as `review`."""
    store = Store(Path(args.path))
    judge = get_judge(args.judge)

    all_reviews = store.all()
    if args.all:
        targets = all_reviews
    else:
        gi = load_guidelines()
        targets = [r for r in all_reviews
                   if is_stale(r, transcript=(store.get(r.transcript_id) or (None, None))[0],
                                  guidelines=gi)]
    if not targets:
        print("nothing to rereview", file=sys.stderr)
        return 0
    print(f"judge={judge.name}  rereview={len(targets)}  out={args.path}", file=sys.stderr)
    errors = 0
    t0 = time.time()
    for r in targets:
        hit = store.get(r.transcript_id)
        if hit is None:
            errors += 1
            print(f"x {r.transcript_id}: transcript missing from store", file=sys.stderr)
            continue
        t, _ = hit
        try:
            new_r = judge.judge(t)
        except JudgeRejected as e:
            store.flush()
            print(f"x {t.id}: JudgeRejected: {e}", file=sys.stderr)
            print("stopping: judge rejected the request (config-scope). Fix credentials or model and retry.", file=sys.stderr)
            sys.exit(3)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"x {t.id}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        store.put(t, new_r)
        flag = "!" if new_r.needs_human_review else " "
        print(f"{flag} {t.id:28s} rescored", file=sys.stderr)
    store.flush()
    print(f"done: {len(targets) - errors} rescored, {errors} failed, {time.time() - t0:.1f}s", file=sys.stderr)
    if errors:
        sys.exit(1)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cqr")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("review", help="review a batch of transcripts")
    r.add_argument("--abcd", help="path to ABCD data dir (with guidelines.json)")
    r.add_argument("--split", default="dev")
    r.add_argument("--limit", type=int, default=15,
                   help="max ABCD conversations to load (default: 15, matches the README example)")
    r.add_argument("--offset", type=int, default=0)
    r.add_argument("--synthetic", help="path to synthetic.jsonl")
    r.add_argument("--jsonl", help="any JSONL of Transcript objects")
    r.add_argument("--judge", choices=["llm", "heuristic", "anthropic"], default=None,
                   help="default: llm if ANTHROPIC/OPENAI key or CQR_LLM_BASE_URL is set, else heuristic. 'anthropic' is a back-compat alias for 'llm'.")
    r.add_argument("--out", default="out/reviews.json",
                   help="review store; merges by transcript id across runs")
    r.add_argument("--fresh", action="store_true",
                   help="delete --out before starting; otherwise reviews merge by transcript id")
    r.add_argument("--concurrency", type=int, default=1,
                   help="run judge calls in parallel across N threads (default: 1)")
    r.add_argument("--force", action="store_true",
                   help="bypass the content cache and re-run the judge for every transcript")
    r.set_defaults(fn=cmd_review)

    s = sub.add_parser("show", help="print a stored review set, riskiest first")
    s.add_argument("path", nargs="?", default="out/reviews.json")
    s.add_argument("--needs-review", action="store_true",
                   help="only show reviews flagged for human review (medium+ flag OR contradicted)")
    s.add_argument("--source", help="filter to one source: abcd | synthetic | upload | ...")
    s.add_argument("--json", action="store_true", help="emit the sorted list as JSON to stdout")
    s.set_defaults(fn=cmd_show)

    re = sub.add_parser("rereview",
                        help="re-run the judge for stored reviews that have gone stale")
    re.add_argument("path", nargs="?", default="out/reviews.json")
    group = re.add_mutually_exclusive_group()
    group.add_argument("--stale", action="store_true",
                       help="rereview only reviews the current rubric/schema/guidelines mark stale (default)")
    group.add_argument("--all", action="store_true",
                       help="rereview every stored review, stale or not")
    re.add_argument("--judge", choices=["llm", "heuristic", "anthropic"], default=None)
    re.set_defaults(fn=cmd_rereview)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
