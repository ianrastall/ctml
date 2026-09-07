r"""Merge per-source crosstable JSON arrays into the consolidated dataset.

Every reader emits an array of crosstable objects in the shared contract
(docs/crosstable-format.md); each object carries its own `source` and `ref`
provenance. Consolidation is therefore plain concatenation: this script
reads any number of per-source arrays and writes one combined array.

Usage:
    python merge_crosstables.py --out D:\ctml\crosstables\crosstables.json in1.json in2.json ...
    python merge_crosstables.py --refresh build\crosstables\olimpbase.json

--refresh updates the consolidated file in place: records whose `source`
appears in the given file(s) are dropped and replaced by the new records.

Prints a per-source record count for the merged output so the result can be
checked against the inputs.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parents[1] / 'crosstables/crosstables.json'


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", help="per-source crosstable JSON arrays")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="replace the input files' sources inside the existing --out file",
    )
    args = ap.parse_args()

    merged: list[dict] = []
    if args.refresh:
        with Path(args.out).open(encoding="utf-8") as handle:
            merged = json.load(handle)
        print(f"{args.out}: {len(merged)} existing crosstables")
    for path in args.inputs:
        with Path(path).open(encoding="utf-8") as handle:
            tables = json.load(handle)
        if not isinstance(tables, list):
            raise SystemExit(f"{path}: expected a JSON array of crosstables")
        print(f"{path}: {len(tables)} crosstables")
        if args.refresh:
            stale = {t.get("source", "?") for t in tables}
            before = len(merged)
            merged = [t for t in merged if t.get("source", "?") not in stale]
            print(f"  dropped {before - len(merged)} stale records for {sorted(stale)}")
        merged.extend(tables)

    counts = Counter(t.get("source", "?") for t in merged)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=1)

    print(f"\n{out}: {len(merged)} crosstables")
    for source, count in counts.most_common():
        print(f"  {source}: {count}")


if __name__ == "__main__":
    main()
