#!/usr/bin/env python3
"""Read player-like OlimpBase standings into the crosstable contract.

OlimpBase mixes individual standings and team standings. CTML tournament
participants are currently playerRefs, so this reader emits CTML only for
tables that look like individual/player standings. Team standings are counted
and can be included in JSON for audit with --include-team-json.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from ctml_source_common import (
    MONTHS,
    TITLE_VALUES,
    extract_tables,
    normalize_fed,
    normalize_space,
    parse_points,
    parse_text_date_range,
    read_text,
    write_ctml_files,
)


DEFAULT_ROOT = Path(__file__).resolve().parents[1] / 'crosstables/raw/olimpbase'
DEFAULT_JSON = Path(__file__).resolve().parents[1] / 'build/crosstables/olimpbase.json'
DEFAULT_CTML = Path(__file__).resolve().parents[1] / 'build/drafts/olimpbase'


def strip_tags(fragment: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", " ", fragment))


def page_heading(text: str) -> str:
    match = re.search(r'<td[^>]*class=["\']?gi["\']?[^>]*>(.*?)</td>', text, re.I | re.S)
    if match:
        return strip_tags(match.group(1))
    match = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    return strip_tags(match.group(1)) if match else ""


def section_heading(text: str) -> str:
    for body in re.findall(r"<h2[^>]*>(.*?)</h2>", text, re.I | re.S):
        value = strip_tags(body)
        if value and value.lower() not in {"standings", "basic data", "information"}:
            return value
    return ""


def year_from_text(value: str) -> int | None:
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", value)]
    return years[-1] if years else None


def manifest_rows(root: Path) -> list[dict[str, str]]:
    path = root / "manifest.csv"
    out = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            if row.get("status") == "ok" and row.get("is_crosstable") == "yes":
                out.append(row)
    return out


def local_path(root: Path, manifest_value: str) -> Path:
    rel = Path(manifest_value)
    if rel.is_absolute():
        return rel
    # Manifests written by the collector prefix paths with the capture root's
    # folder name at capture time ("olimpbase-crosstables"); anchor on the
    # "html" component instead so the capture tree can be relocated.
    parts = [p.lower() for p in rel.parts]
    if "html" in parts:
        rel = Path(*rel.parts[parts.index("html") :])
    return root / rel


def info_page_for(path: Path) -> Path | None:
    match = re.match(r"(.+?)(fa|fb|ea|eb|ec|ed|ee|ef|eg|eh|ei|fj|fk|fl|fm|fn).*\.html?$", path.name, re.I)
    if not match:
        return None
    candidate = path.with_name(f"{match.group(1)}in.html")
    return candidate if candidate.exists() else None


def info_data(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    tables = extract_tables(read_text(path))
    data: dict[str, str] = {}
    for rows in tables:
        for row in rows:
            if len(row) >= 2 and row[0].rstrip(":"):
                key = normalize_space(row[0]).lower().rstrip(":")
                data.setdefault(key, normalize_space(row[1]))
    return data


MONTH_ALTERNATION = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_ALTERNATION})\s+(\d{{4}})\b", re.I
)
DATE_NOISE_RE = re.compile(r"modified|updated|created|copyright|&copy;", re.I)
TITLE_PREFIX_RE = re.compile(
    r"^(" + "|".join(sorted(TITLE_VALUES, key=len, reverse=True)) + r")\s+"
)
TRAILING_YEAR_RE = re.compile(r"\s*\d{4}(?:\s*/\s*\d{2,4})?\s*$")


def page_date_range(text: str) -> tuple[str, str] | None:
    """Min/max of the day-precision dates on the page (the round schedule),
    ignoring dates that follow page-maintenance phrasing."""
    found = []
    for match in DATE_RE.finditer(text):
        context = text[max(0, match.start() - 60) : match.start()]
        if DATE_NOISE_RE.search(context):
            continue
        day, month, year = int(match[1]), MONTHS[match[2].lower()], int(match[3])
        if 1 <= day <= 31:
            found.append((year, month, day))
    if not found:
        return None
    iso = lambda t: f"{t[0]:04}-{t[1]:02}-{t[2]:02}"
    return iso(min(found)), iso(max(found))


def split_event_heading(heading: str) -> tuple[str, str]:
    """OlimpBase individual pages head with 'Event :: Place Year'."""
    if "::" not in heading:
        return heading, ""
    event, _, place = heading.partition("::")
    place = TRAILING_YEAR_RE.sub("", normalize_space(place))
    return normalize_space(event), place


def parse_individual_page(text: str, path: Path, row: dict[str, str]) -> dict[str, Any] | None:
    """Parse the individual-tournament layout (ind-* sections): a standings
    table headed pos./name/Elo/flag/.../pts. Works over raw HTML so the
    federation can be read from the flag image filename, which the generic
    cell extractor discards."""
    for table_html in re.findall(r"<table[^>]*>.*?</table>", text, re.I | re.S):
        raw_rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.I | re.S)
        parsed_rows = []
        for raw in raw_rows:
            cells = [
                normalize_space(strip_tags(c))
                for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", raw, re.I | re.S)
            ]
            flag = re.search(r"flagi/([a-z0-9_]+)\.gif", raw, re.I)
            parsed_rows.append((cells, flag.group(1).upper() if flag else ""))

        header_idx = None
        for idx, (cells, _) in enumerate(parsed_rows[:4]):
            low = [c.lower().rstrip(".:") for c in cells]
            if "name" in low and any(c in {"pos", "no", "rank"} for c in low):
                if any("team" in c for c in low):
                    break  # team standings; not this parser's job
                header_idx = idx
                break
        if header_idx is None:
            continue

        labels = [c.lower().rstrip(".:") for c in parsed_rows[header_idx][0]]
        name_col = labels.index("name")
        rank_col = next(i for i, c in enumerate(labels) if c in {"pos", "no", "rank"})
        elo_col = labels.index("elo") if "elo" in labels else None
        score_col = None
        for key in ("pts", "points", "score"):
            if key in labels:
                score_col = labels.index(key)
                break
        if score_col is None:
            continue

        players = []
        previous_rank = None
        for cells, flag in parsed_rows[header_idx + 1 :]:
            if len(cells) <= max(name_col, score_col):
                continue
            name = cells[name_col]
            if not name or name in {"+", "=", "-"}:
                continue
            score = parse_points(cells[score_col])
            if score is None:
                continue
            rank_match = re.search(r"\d+", cells[rank_col]) if rank_col < len(cells) else None
            rank = int(rank_match.group(0)) if rank_match else previous_rank
            previous_rank = rank
            title_match = TITLE_PREFIX_RE.match(name)
            title = title_match.group(1) if title_match else ""
            if title_match:
                name = name[title_match.end() :].strip()
            rating = None
            if elo_col is not None and elo_col < len(cells):
                digits = re.search(r"\d{3,4}", cells[elo_col])
                rating = int(digits.group(0)) if digits else None
            players.append(
                {
                    "rank": rank,
                    "name": name,
                    "title": title,
                    "fed": normalize_fed(flag),
                    "rating": rating,
                    "score": score,
                }
            )
        if len(players) < 2:
            continue

        heading = page_heading(text)
        event, place = split_event_heading(heading)
        dates = page_date_range(text)
        if dates is None:
            year = year_from_text(heading) or year_from_text(path.name)
            if year is None:
                return None
            dates = (str(year), str(year))
        numeric_labels = sum(1 for c in labels if c.isdigit())
        if numeric_labels >= 3:
            fmt = "round-robin"
        elif any(c.startswith("buch") or c == "games" for c in labels):
            fmt = "swiss"
        else:
            fmt = "unknown"
        return {
            "source": "olimpbase",
            "reader": "olimpbase_crosstables/0.2",
            "ref": f"{path.parent.name}/{path.with_suffix('').name}",
            "event": event,
            "place": place,
            "start": dates[0],
            "end": dates[1],
            "format": fmt,
            "classification": "individual",
            "rating_system": "fide" if any(p["rating"] for p in players) else "unknown",
            "url": row.get("url", ""),
            "source_path": str(path),
            "notes": "",
            "players": players,
        }
    return None


def find_standings_table(tables: list[list[list[str]]]) -> tuple[list[list[str]], int] | None:
    for rows in tables:
        for idx, row in enumerate(rows[:5]):
            low = [normalize_space(c).lower() for c in row]
            if any(c in {"pos.", "pos", "no.", "no", "rank"} for c in low) and any(
                c.startswith("team") or c in {"player", "name"} for c in low
            ):
                return rows, idx
    return None


def column(labels: list[str], names: set[str], contains: set[str] = set()) -> int | None:
    for idx, label in enumerate(labels):
        low = normalize_space(label).lower().rstrip(".:")
        if low in names or any(part in low for part in contains):
            return idx
    return None


def classify_rows(players: list[dict[str, Any]], labels: list[str]) -> str:
    label_text = " ".join(labels).lower()
    comma_names = sum(1 for p in players if "," in p["name"])
    if players and comma_names / len(players) >= 0.45:
        return "individual"
    if "player" in label_text or "name" in label_text:
        return "individual"
    return "team"


def parse_page(path: Path, row: dict[str, str]) -> dict[str, Any] | None:
    text = read_text(path)
    individual = parse_individual_page(text, path, row)
    if individual is not None:
        return individual
    tables = extract_tables(text)
    found = find_standings_table(tables)
    if not found:
        return None
    rows, header_idx = found
    labels = rows[header_idx]
    rank_col = column(labels, {"pos", "pos.", "no", "no.", "rank"}) or 0
    name_col = column(labels, {"team", "team seed", "player", "name"}, {"team", "player"})
    code_col = column(labels, {"code", "fed", "country"})
    score_col = column(labels, {"pts", "points", "score", "∑"}, {"pts"})
    if name_col is None or code_col is None or score_col is None:
        return None

    players = []
    previous_rank = None
    for data in rows[header_idx + 1 :]:
        if len(data) <= max(name_col, code_col, score_col):
            continue
        name = normalize_space(data[name_col])
        if not name or name in {"+", "=", "-"}:
            continue
        score = parse_points(data[score_col])
        if score is None:
            continue
        rank = re.match(r"\d+", normalize_space(data[rank_col])) if rank_col < len(data) else None
        rank_num = int(rank.group(0)) if rank else previous_rank
        previous_rank = rank_num
        team_seed = ""
        seed_match = re.search(r"\((\d+)\)\s*$", name)
        if seed_match:
            team_seed = seed_match.group(1)
            name = re.sub(r"\(\d+\)\s*$", "", name).strip()
        players.append(
            {
                "rank": rank_num,
                "name": name,
                "fed": normalize_fed(data[code_col]),
                "score": score,
                "seed": team_seed,
            }
        )
    if not players:
        return None

    info = info_data(info_page_for(path))
    heading = page_heading(text)
    section = section_heading(text)
    event = heading
    if section:
        event = f"{heading} - {section}"
    fallback_year = year_from_text(event) or year_from_text(path.name)
    date_raw = info.get("date") or info.get("dates")
    dates = parse_text_date_range(date_raw, fallback_year) if date_raw else None
    if dates is None and fallback_year:
        dates = parse_text_date_range(str(fallback_year), fallback_year)
    if dates is None:
        return None
    start, end = dates
    classification = classify_rows(players, labels)
    city = info.get("city", "").rstrip(",")
    notes = []
    if date_raw:
        notes.append(f"dates_raw={date_raw}")
    for key in ("game system", "competition format", "time control"):
        if info.get(key):
            notes.append(f"{key}={info[key]}")
    return {
        "source": "olimpbase",
        "reader": "olimpbase_crosstables/0.1",
        "ref": path.with_suffix("").name,
        "event": event,
        "place": city,
        "start": str(start.y) if start.precision == "year" else f"{start.y:04}-{start.m or 1:02}-{start.d or 1:02}",
        "end": str(end.y) if end.precision == "year" else f"{end.y:04}-{end.m or 1:02}-{end.d or 1:02}",
        "format": "team" if classification == "team" else "round-robin",
        "classification": classification,
        "rating_system": "unknown",
        "url": row.get("url", ""),
        "source_path": str(path),
        "notes": "; ".join(notes),
        "players": players,
    }


def read_root(root: Path, include_team_json: bool = False) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    stats = {"manifest_crosstables": 0, "individual": 0, "team_skipped": 0, "info_page_skipped": 0, "failed": 0}
    for row in manifest_rows(root):
        stats["manifest_crosstables"] += 1
        path = local_path(root, row.get("local_path", ""))
        if not path.exists():
            stats["failed"] += 1
            continue
        if path.stem.lower().endswith("in"):
            stats["info_page_skipped"] += 1
            continue
        table = parse_page(path, row)
        if table is None:
            stats["failed"] += 1
            continue
        if table.get("classification") == "team":
            stats["team_skipped"] += 1
            if include_team_json:
                out.append(table)
            continue
        out.append(table)
        stats["individual"] += 1
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=str(DEFAULT_ROOT))
    ap.add_argument("--out", default=str(DEFAULT_JSON))
    ap.add_argument("--ctml-out", default=str(DEFAULT_CTML))
    ap.add_argument("--include-team-json", action="store_true")
    ap.add_argument("--no-ctml", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    tables, stats = read_root(Path(args.root), include_team_json=args.include_team_json)
    print(json.dumps(stats, indent=2), file=sys.stderr)
    if args.summary:
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(tables, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {len(tables)} crosstables to {out}", file=sys.stderr)
    if not args.no_ctml:
        individual = [t for t in tables if t.get("classification") != "team"]
        written, skipped = write_ctml_files(individual, Path(args.ctml_out), clear=True)
        print(f"wrote {written} CTML files to {args.ctml_out}; skipped {skipped}", file=sys.stderr)
    else:
        print(json.dumps(tables, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
