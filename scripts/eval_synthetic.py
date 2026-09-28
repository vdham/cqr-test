"""
Tiny eval: compare stored reviews for the synthetic set against the labels in
metadata.expect. Not a benchmark; it is the seed of one.

`|` in an expect value means OR: `correctness=unverifiable|contradicted` passes
if the review's correctness matches either. Run `python -m cqr.cli review ...`
first — this script is offline; it does NOT call any model.

  python scripts/eval_synthetic.py                        # reads out/reviews.json
  python scripts/eval_synthetic.py --store PATH           # reads a different store
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cqr.store import Store  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--store", default="out/reviews.json",
                   help="path to a review store written by `python -m cqr.cli review`")
    args = p.parse_args(argv)

    store = Store(Path(args.store))
    reviews = [r for r in store.all() if r.source == "synthetic"]
    if not reviews:
        print(f"no synthetic reviews in {args.store}; run `python -m cqr.cli review --synthetic ...` first")
        return 1

    # Header: what was scored, by whom, against which rubric version.
    judges = sorted({r.judge for r in reviews})
    rubric_versions = sorted({r.rubric_version for r in reviews})
    print(f"store={args.store}  synthetic reviews={len(reviews)}")
    print(f"judge(s)={', '.join(judges)}  rubric_version(s)={', '.join(rubric_versions)}")

    rows, hits, total = [], 0, 0
    for r in sorted(reviews, key=lambda r: r.transcript_id):
        t, _ = store.get(r.transcript_id)
        expect = t.metadata.get("expect", "")
        checks = []
        for m in re.finditer(r"(resolution|correctness|effort|quality|risk)=([a-z_+|]+)", expect):
            k, want = m.groups()
            got = {
                "resolution": r.resolution.level.value,
                "correctness": r.correctness.level.value,
                "effort": r.customer_effort.level.value,
                "quality": r.interaction_quality.level.value,
                "risk": "+".join(sorted(f.type.value for f in r.risk_flags)) or "none",
            }[k]
            wants = want.split("|")
            if k == "risk":
                ok = any(all(x in got for x in w.split("+")) for w in wants)
            else:
                ok = got in wants
            checks.append((k, want, got, ok))
            total += 1
            hits += ok
        rows.append((r.transcript_id, r.judge, checks))

    for id_, judge, checks in rows:
        print(f"\n{id_}  [{judge}]")
        for k, want, got, ok in checks:
            print(f"   {'✓' if ok else '✗'} {k:12s} want={want:28s} got={got}")
    print(f"\n{hits}/{total} labeled checks passed")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
