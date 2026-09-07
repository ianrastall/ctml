#!/usr/bin/env python3
"""Read screened Chess-Results exports into the crosstable contract.

The complete Chess-Results export folders contain XLSX workbooks. This reader
uses the final ranking workbook for rank/score and the starting-rank workbook
for FIDE IDs and source ratings, keyed by start number.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

from ctml_source_common import (
    infer_cadence,
    map_event_format,
    normalize_fed,
    normalize_space,
    parse_points,
    parse_yyyymmdd,
    to_int,
    write_ctml_files,
    xlsx_rows,
)


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / 'crosstables/raw/chess-results'
DEFAULT_JSON = Path(__file__).resolve().parents[1] / 'build/crosstables/chess-results.json'
DEFAULT_CTML = Path(__file__).resolve().parents[1] / 'build/drafts/chess-results'


def label_map(row: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for idx, value in enumerate(row):
        key = normalize_space(value).lower().strip(".:")
        if key:
            out.setdefault(key, idx)
    return out


def find_header(rows: list[list[str]], required: set[str]) -> tuple[int, dict[str, int]] | None:
    for idx, row in enumerate(rows):
        labels = label_map(row)
        if required.issubset(labels):
            return idx, labels
    return None


def cell(row: list[str], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    return normalize_space(row[idx])


def parse_starting_rank(path: Path) -> dict[int, dict[str, Any]]:
    rows = xlsx_rows(path)
    found = find_header(rows, {"name", "fideid", "fed"})
    if not found:
        return {}
    header_idx, labels = found
    no_col = labels.get("no") or labels.get("sno") or 0
    name_col = labels["name"]
    fide_col = labels["fideid"]
    fed_col = labels["fed"]
    rating_col = labels.get("rtgi") or labels.get("rtg") or labels.get("rating")
    type_col = labels.get("typ")
    sex_col = labels.get("sex")
    age_col = labels.get("age")
    club_col = labels.get("club/city") or labels.get("club") or labels.get("city")
    title_col = name_col - 1 if name_col > 0 else None

    out: dict[int, dict[str, Any]] = {}
    for row in rows[header_idx + 1 :]:
        seed = to_int(cell(row, no_col))
        name = cell(row, name_col)
        if seed is None or not name:
            continue
        out[seed] = {
            "seed": seed,
            "name": name,
            "title": cell(row, title_col),
            "fide_id": cell(row, fide_col),
            "fed": normalize_fed(cell(row, fed_col)),
            "rating": to_int(cell(row, rating_col)),
            "type": cell(row, type_col),
            "sex": cell(row, sex_col),
            "age": cell(row, age_col),
            "club": cell(row, club_col),
        }
    return out


def parse_final_ranking(path: Path, starting: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = xlsx_rows(path)
    found = find_header(rows, {"name", "fed"})
    if not found:
        return []
    header_idx, labels = found
    rank_col = labels.get("rk") or labels.get("rank") or 0
    seed_col = labels.get("sno") or labels.get("no")
    name_col = labels["name"]
    fed_col = labels["fed"]
    rating_col = labels.get("rtgi") or labels.get("rtg") or labels.get("rating")
    score_col = labels.get("pts") or labels.get("pts ") or labels.get("points") or labels.get("score")
    type_col = labels.get("typ")
    sex_col = labels.get("sex")
    club_col = labels.get("club/city") or labels.get("club") or labels.get("city")
    title_col = name_col - 1 if name_col > 0 else None

    players: list[dict[str, Any]] = []
    for row in rows[header_idx + 1 :]:
        name = cell(row, name_col)
        if not name:
            continue
        seed = to_int(cell(row, seed_col))
        from_start = starting.get(seed or -1, {})
        fed = normalize_fed(cell(row, fed_col)) or from_start.get("fed", "")
        rating = to_int(cell(row, rating_col))
        if rating is None:
            rating = from_start.get("rating")
        player = {
            "rank": to_int(cell(row, rank_col)),
            "seed": seed,
            "source_id": seed,
            "name": name,
            "title": cell(row, title_col) or from_start.get("title", ""),
            "fed": fed,
            "rating": rating,
            "score": parse_points(cell(row, score_col)),
            "fide_id": from_start.get("fide_id", ""),
            "type": cell(row, type_col) or from_start.get("type", ""),
            "sex": cell(row, sex_col) or from_start.get("sex", ""),
            "club": cell(row, club_col) or from_start.get("club", ""),
        }
        players.append(player)
    return players


def load_download_manifest(root: Path) -> dict[str, dict[str, Any]]:
    manifest = root / "download_manifest.csv"
    out: dict[str, dict[str, Any]] = {}
    if not manifest.exists():
        return out
    with manifest.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") != "ok":
                continue
            key = row.get("db_key") or ""
            if not key:
                continue
            entry = out.setdefault(
                key,
                {
                    "db_key": key,
                    "title": row.get("title", ""),
                    "system": row.get("system", ""),
                    "from": row.get("from", ""),
                    "to": row.get("to", ""),
                    "urls": {},
                    "paths": {},
                },
            )
            page_type = row.get("page_type") or ""
            if page_type:
                entry["urls"][page_type] = row.get("url", "")
                entry["paths"][page_type] = row.get("local_path", "")
            for field in ("title", "system", "from", "to"):
                if not entry.get(field) and row.get(field):
                    entry[field] = row[field]
    return out


def load_kept_keys(root: Path) -> set[str]:
    path = root / "kept_db_keys.txt"
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}


def read_root(root: Path, only_kept: bool = True) -> tuple[list[dict[str, Any]], dict[str, int]]:
    manifest = load_download_manifest(root)
    kept = load_kept_keys(root)
    export_root = root / "exports"
    tables: list[dict[str, Any]] = []
    stats = {
        "folders_seen": 0,
        "not_kept": 0,
        "missing_final_ranking": 0,
        "missing_dates": 0,
        "empty_players": 0,
        "emitted": 0,
    }
    for folder in sorted(export_root.glob("tnr*")):
        if not folder.is_dir():
            continue
        key = folder.name.removeprefix("tnr")
        stats["folders_seen"] += 1
        if only_kept and kept and key not in kept:
            stats["not_kept"] += 1
            continue
        final_path = folder / f"tnr{key}_final_ranking.xlsx"
        if not final_path.exists():
            stats["missing_final_ranking"] += 1
            continue
        meta = manifest.get(key, {"db_key": key, "title": folder.name, "system": "", "from": "", "to": "", "urls": {}})
        start = parse_yyyymmdd(meta.get("from"))
        end = parse_yyyymmdd(meta.get("to")) or start
        if not start:
            stats["missing_dates"] += 1
            continue
        starting_path = folder / f"tnr{key}_starting_rank.xlsx"
        starting = parse_starting_rank(starting_path) if starting_path.exists() else {}
        players = parse_final_ranking(final_path, starting)
        if not players:
            stats["empty_players"] += 1
            continue
        title = normalize_space(meta.get("title")) or f"Chess-Results tnr{key}"
        urls = meta.get("urls", {})
        table = {
            "source": "chess-results",
            "reader": "chess_results_crosstables/0.1",
            "ref": f"tnr{key}",
            "event": title,
            "start": f"{start.y:04}-{start.m or 1:02}-{start.d or 1:02}" if start.d else str(start.y),
            "end": f"{end.y:04}-{end.m or 1:02}-{end.d or 1:02}" if end and end.d else str(end.y if end else start.y),
            "format": map_event_format(meta.get("system")),
            "cadence": infer_cadence(title) or "unknown",
            "rating_system": "fide",
            "url": urls.get("final_ranking") or urls.get("tournament_details") or "",
            "source_path": str(final_path),
            "notes": f"Chess-Results system={normalize_space(meta.get('system'))}; db_key={key}",
            "players": players,
        }
        tables.append(table)
        stats["emitted"] += 1
    return tables, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=str(DEFAULT_ROOT))
    ap.add_argument("--out", default=str(DEFAULT_JSON))
    ap.add_argument("--ctml-out", default=str(DEFAULT_CTML))
    ap.add_argument("--all-complete", action="store_true", help="Use every complete export folder, not only kept keys.")
    ap.add_argument("--no-ctml", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    root = Path(args.root)
    tables, stats = read_root(root, only_kept=not args.all_complete)
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
