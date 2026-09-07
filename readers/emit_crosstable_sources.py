#!/usr/bin/env python3
"""Emit CTML tournament source drafts from crosstable-contract JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ctml_source_common import write_ctml_files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--out", required=True)
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    with Path(args.json_path).open(encoding="utf-8") as handle:
        tables = json.load(handle)
    if args.offset or args.limit is not None:
        end = None if args.limit is None else args.offset + args.limit
        tables = tables[args.offset : end]
    for idx, table in enumerate(tables, start=args.offset + 1):
        if isinstance(table, dict):
            table["_filename_prefix"] = f"{idx:06d}"
    written, skipped = write_ctml_files(tables, Path(args.out), clear=args.clear)
    print(f"wrote {written} CTML files to {args.out}; skipped {skipped}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
