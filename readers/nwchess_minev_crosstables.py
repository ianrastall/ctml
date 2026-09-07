#!/usr/bin/env python3
"""Read Northwest Chess / Minev HTML crosstables into the contract."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from ctml_source_common import (
    extract_tables,
    normalize_space,
    parse_points,
    parse_text_date_range,
    read_text,
    split_score_text,
    write_ctml_files,
)


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / 'crosstables/raw/nwchess-minev'
DEFAULT_JSON = Path(__file__).resolve().parents[1] / 'build/crosstables/nwchess-minev.json'
DEFAULT_CTML = Path(__file__).resolve().parents[1] / 'build/drafts/nwchess-minev'


def manifest_by_path(root: Path) -> dict[str, dict[str, str]]:
    path = root / "manifest.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            local = row.get("local_path") or ""
            if local:
                out[Path(local).name.lower()] = row
    return out


def year_from_name(value: str) -> int | None:
    match = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", value)
    return int(match.group(1)) if match else None


def likely_title_row(row: list[str]) -> bool:
    if len(row) > 4:
        non_empty = [c for c in row if c]
        return len(non_empty) == 1 and bool(year_from_name(non_empty[0]))
    return False


def first_value_after_label(rows: list[list[str]], label: str) -> str:
    wanted = label.lower().rstrip(":")
    for row in rows:
        if not row:
            continue
        first = normalize_space(row[0]).lower().rstrip(":")
        if first == wanted:
            return normalize_space(" ".join(row[1:]))
    return ""


def parse_nwchess_table(rows: list[list[str]], path: Path, meta: dict[str, str]) -> dict[str, Any] | None:
    header_idx = None
    for idx, row in enumerate(rows):
        if row and normalize_space(row[0]).lower() == "player":
            header_idx = idx
            break
    if header_idx is None:
        return None
    header = rows[header_idx]
    total_col = None
    for idx, value in enumerate(header):
        low = normalize_space(value).lower()
        if "total" in low or "score" in low:
            total_col = idx
            break
    if total_col is None:
        return None

    title = normalize_space(meta.get("title")) or path.stem
    for row in rows[:header_idx]:
        if likely_title_row(row):
            title = next(c for c in row if c)
            break
    fallback_year = year_from_name(title) or year_from_name(path.stem)
    dates_raw = first_value_after_label(rows[:header_idx], "Dates")
    date_range = parse_text_date_range(dates_raw, fallback_year) if dates_raw else None
    if date_range is None and fallback_year:
        date_range = parse_text_date_range(str(fallback_year), fallback_year)
    if date_range is None:
        return None
    start, end = date_range

    players = []
    for row in rows[header_idx + 1 :]:
        if total_col >= len(row):
            continue
        name = normalize_space(row[0])
        if not name or name.lower() in {"player", "totals", "total"}:
            continue
        score = parse_points(row[total_col])
        if score is None:
            continue
        players.append(
            {
                "rank": len(players) + 1,
                "name": name,
                "score": score,
            }
        )
    if not players:
        return None

    note = first_value_after_label(rows[:header_idx], "Note")
    source_note = first_value_after_label(rows[:header_idx], "Source")
    notes = []
    if dates_raw:
        notes.append(f"dates_raw={dates_raw}")
    if note:
        notes.append(note)
    if source_note:
        notes.append(f"source={source_note}")
    place = re.sub(r"\b(1[5-9]\d{2}|20\d{2})\b", "", title).strip(" ,-")
    return {
        "source": "nwchess-minev",
        "reader": "nwchess_minev_crosstables/0.1",
        "ref": path.stem,
        "event": title,
        "place": place,
        "start": str(start.y) if start.precision == "year" else f"{start.y:04}-{start.m or 1:02}-{start.d or 1:02}",
        "end": str(end.y) if end.precision == "year" else f"{end.y:04}-{end.m or 1:02}-{end.d or 1:02}",
        "format": "round-robin",
        "rating_system": "unknown",
        "url": meta.get("url", ""),
        "source_path": str(path),
        "notes": "; ".join(notes),
        "players": players,
    }


def read_file(path: Path, meta: dict[str, str]) -> dict[str, Any] | None:
    tables = extract_tables(read_text(path))
    candidates = []
    for rows in tables:
        parsed = parse_nwchess_table(rows, path, meta)
        if parsed:
            candidates.append(parsed)
    if not candidates:
        return None
    candidates.sort(key=lambda t: len(t["players"]), reverse=True)
    return candidates[0]


def read_root(root: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    files_dir = root / "files"
    meta = manifest_by_path(root)
    tables: list[dict[str, Any]] = []
    stats = {"files_seen": 0, "emitted": 0, "failed": 0}
    for path in sorted(files_dir.glob("*.htm*")):
        stats["files_seen"] += 1
        table = read_file(path, meta.get(path.name.lower(), {}))
        if table is None:
            stats["failed"] += 1
            continue
        tables.append(table)
        stats["emitted"] += 1
    return tables, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=str(DEFAULT_ROOT))
    ap.add_argument("--out", default=str(DEFAULT_JSON))
    ap.add_argument("--ctml-out", default=str(DEFAULT_CTML))
    ap.add_argument("--no-ctml", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    tables, stats = read_root(Path(args.root))
    print(json.dumps(stats, indent=2), file=sys.stderr)
    if args.summary:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tables)} crosstables to {out}", file=sys.stderr)
    if not args.no_ctml:
        written, skipped = write_ctml_files(tables, Path(args.ctml_out), clear=True)
        print(f"wrote {written} CTML files to {args.ctml_out}; skipped {skipped}", file=sys.stderr)
    else:
        print(json.dumps(tables, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
