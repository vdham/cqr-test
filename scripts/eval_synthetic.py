"""
Tiny eval: compare stored reviews for the synthetic set against the labels in
metadata.expect. Not a benchmark; it is the seed of one. Run after `review`.

  python scripts/eval_synthetic.py out/reviews.json
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cqr.store import Store  # noqa: E402

store = Store(Path(sys.argv[1] if len(sys.argv) > 1 else "out/reviews.json"))
rows, hits, total = [], 0, 0
for r in sorted(store.all(), key=lambda r: r.transcript_id):
    if r.source != "synthetic":
        continue
    t, _ = store.get(r.transcript_id)
    expect = t.metadata.get("expect", "")
    checks = []
    for m in re.finditer(r"(resolution|correctness|effort|quality|risk)=([a-z_+]+)", expect):
        k, v = m.groups()
        got = {
            "resolution": r.resolution.level,
            "correctness": r.correctness.level,
            "effort": r.customer_effort.level,
            "quality": r.interaction_quality.level,
            "risk": "+".join(sorted(f.type.value for f in r.risk_flags)) or "none",
        }[k]
        ok = (got == v) if k != "risk" else all(x in got for x in v.split("+"))
        checks.append((k, v, got, ok))
        total += 1; hits += ok
    rows.append((r.transcript_id, r.judge, checks))

for id_, judge, checks in rows:
    print(f"\n{id_}  [{judge}]")
    for k, want, got, ok in checks:
        print(f"   {'✓' if ok else '✗'} {k:12s} want={want:28s} got={got}")
print(f"\n{hits}/{total} labeled checks passed")
