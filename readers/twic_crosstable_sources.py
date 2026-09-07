#!/usr/bin/env python3
"""Join TWIC crosstable reader output to dates/event refs and emit CTML drafts."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import twic_crosstables
import twic_crosstables_pre
from ctml_source_common import (
    PartialDate,
    normalize_space,
    parse_iso_date,
    read_text,
    write_ctml_files,
)


DEFAULT_HTML = Path(r"D:\TWIC_HTML")
DEFAULT_EVENTS = Path(__file__).resolve().parents[1] / 'sources/twic/twic-events.xml'
DEFAULT_JSON = Path(__file__).resolve().parents[1] / 'build/crosstables/twic.json'
DEFAULT_CTML = Path(__file__).resolve().parents[1] / 'build/drafts/twic'
NS = {"ctml": "urn:ctml:2.0"}
ROMAN_MONTHS = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
}


@dataclass
class EventRecord:
    ref: str
    names: list[str]
    start: PartialDate
    end: PartialDate
    place: str
    country: str
    event_type: str
    cadence: str
    issues: set[int]


@dataclass
class EventIndex:
    records: list[EventRecord]
    by_issue: dict[int, list[EventRecord]]
    by_year_month: dict[tuple[int, int], list[EventRecord]]


def key(value: str) -> str:
    text = normalize_space(value).lower().replace("&", " and ")
    text = re.sub(r"^\d{1,3}(st|nd|rd|th)\s+", "", text)
    text = re.sub(r"\b(19\d{2}|20\d{2})\b", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def token_score(a: str, b: str) -> float:
    aa = set(key(a).split())
    bb = set(key(b).split())
    if not aa or not bb:
        return 0.0
    return len(aa & bb) / len(aa | bb)


def text_score(a: str, b: str) -> float:
    return 0.6 * SequenceMatcher(None, key(a), key(b)).ratio() + 0.4 * token_score(a, b)


def date_from_elem(elem: ET.Element) -> PartialDate | None:
    y = elem.attrib.get("y")
    if not y:
        return None
    return PartialDate(int(y), int(elem.attrib["m"]) if elem.attrib.get("m") else None, int(elem.attrib["d"]) if elem.attrib.get("d") else None)


def parse_issues(notes: str) -> set[int]:
    match = re.search(r"TWIC\s+(\d+)(?:-(\d+))?", notes)
    if not match:
        return set()
    lo = int(match.group(1))
    hi = int(match.group(2) or lo)
    if hi < lo or hi - lo > 500:
        return {lo}
    return set(range(lo, hi + 1))


def load_events(path: Path) -> list[EventRecord]:
    tree = ET.parse(path)
    out: list[EventRecord] = []
    for occ in tree.findall(".//ctml:eventOccurrence", NS):
        ref = occ.attrib.get("ref", "")
        dates = occ.find("ctml:dates", NS)
        if not ref or dates is None:
            continue
        start_elem = dates.find("ctml:start", NS)
        end_elem = dates.find("ctml:end", NS)
        if start_elem is None:
            continue
        start = date_from_elem(start_elem)
        end = date_from_elem(end_elem) if end_elem is not None else start
        if start is None or end is None:
            continue
        names = []
        name = occ.findtext("ctml:name", default="", namespaces=NS)
        if name:
            names.append(name)
        for alias in occ.findall("ctml:aliases/ctml:alias", NS):
            if alias.text:
                names.append(normalize_space(alias.text))
        place = ""
        country = ""
        place_ref = occ.find("ctml:placeRef", NS)
        if place_ref is not None:
            place = place_ref.findtext("ctml:name", default="", namespaces=NS)
            country = place_ref.findtext("ctml:country", default="", namespaces=NS)
        notes = occ.findtext("ctml:notes", default="", namespaces=NS)
        if not place:
            m = re.search(r"place\s+([^;]+)", notes)
            if m:
                place = normalize_space(m.group(1))
        if not country:
            m = re.search(r"federation\s+([A-Z]{3})", notes)
            if m:
                country = m.group(1)
        out.append(
            EventRecord(
                ref=ref,
                names=names,
                start=start,
                end=end,
                place=place,
                country=country,
                event_type=occ.findtext("ctml:eventType", default="", namespaces=NS),
                cadence=occ.findtext("ctml:cadence", default="", namespaces=NS),
                issues=parse_issues(notes),
            )
        )
    return out


def build_event_index(records: list[EventRecord]) -> EventIndex:
    by_issue: dict[int, list[EventRecord]] = {}
    by_year_month: dict[tuple[int, int], list[EventRecord]] = {}
    for record in records:
        for issue in record.issues:
            by_issue.setdefault(issue, []).append(record)
        for date in (record.start, record.end):
            if date.m:
                by_year_month.setdefault((date.y, date.m), []).append(record)
            else:
                for month in range(1, 13):
                    by_year_month.setdefault((date.y, month), []).append(record)
    return EventIndex(records=records, by_issue=by_issue, by_year_month=by_year_month)


def gather_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in {".html", ".htm"}))
        else:
            files.append(path)
    return files


def issue_from_file(path: Path) -> int | None:
    match = re.search(r"twic(\d+)", path.name, re.I)
    if match:
        return int(match.group(1))
    text = read_text(path)
    match = re.search(r"The Week in Chess\s+(\d+)", text, re.I)
    return int(match.group(1)) if match else None


def parse_twic_date(text: str) -> tuple[PartialDate, PartialDate] | None:
    raw = normalize_space(text).lower()
    # Common classic/modern: ", 3-12 vi 2009" or ", 7 v - 16 vi 2006".
    match = re.search(r"(\d{1,2})\s+([ivx]{1,4})\s*-\s*(\d{1,2})\s+([ivx]{1,4})\s+(19\d{2}|20\d{2})", raw)
    if match:
        year = int(match.group(5))
        sm = ROMAN_MONTHS.get(match.group(2))
        em = ROMAN_MONTHS.get(match.group(4))
        if sm and em:
            return PartialDate(year, sm, int(match.group(1))), PartialDate(year, em, int(match.group(3)))
    match = re.search(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?\s+([ivx]{1,4})\s+(19\d{2}|20\d{2})", raw)
    if match:
        start_d = int(match.group(1))
        end_d = int(match.group(2) or match.group(1))
        month = ROMAN_MONTHS.get(match.group(3))
        year = int(match.group(4))
        if month:
            return PartialDate(year, month, start_d), PartialDate(year, month, end_d)
    return None


def date_close(a: tuple[PartialDate, PartialDate] | None, b: EventRecord) -> bool:
    if a is None:
        return False
    return a[0].y == b.start.y and abs((a[0].m or 0) - (b.start.m or 0)) <= 1


def month_neighbors(date: PartialDate) -> list[tuple[int, int]]:
    if not date.m:
        return [(date.y, month) for month in range(1, 13)]
    out = [(date.y, date.m)]
    if date.m > 1:
        out.append((date.y, date.m - 1))
    if date.m < 12:
        out.append((date.y, date.m + 1))
    return out


def find_event(table: dict[str, Any], index: EventIndex, issue: int | None, parsed_dates: tuple[PartialDate, PartialDate] | None) -> EventRecord | None:
    name = normalize_space(table.get("event"))
    if not name:
        return None
    pool: list[EventRecord] = []
    seen_refs: set[str] = set()
    if issue is not None:
        for event in index.by_issue.get(issue, []):
            pool.append(event)
            seen_refs.add(event.ref)
    if parsed_dates:
        for ym in month_neighbors(parsed_dates[0]):
            for event in index.by_year_month.get(ym, []):
                if event.ref not in seen_refs:
                    pool.append(event)
                    seen_refs.add(event.ref)
    if not pool:
        return None
    candidates = []
    for event in pool:
        if issue is not None and event.issues and issue not in event.issues:
            continue
        if parsed_dates and not date_close(parsed_dates, event):
            continue
        best = max((text_score(name, n) for n in event.names), default=0.0)
        if best >= 0.72 or (parsed_dates and best >= 0.55):
            candidates.append((best, event))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def iso(date: PartialDate) -> str:
    if date.precision == "day":
        return f"{date.y:04}-{date.m:02}-{date.d:02}"
    if date.precision == "month":
        return f"{date.y:04}-{date.m:02}"
    return str(date.y)


def read_all(files: list[Path], index: EventIndex) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out: list[dict[str, Any]] = []
    stats = {"files": 0, "raw_tables": 0, "deduped": 0, "matched_event_ref": 0, "date_from_header": 0, "undated": 0, "emitted": 0}
    seen: set[tuple[Any, ...]] = set()
    for path in files:
        stats["files"] += 1
        issue = issue_from_file(path)
        tables = twic_crosstables.read_file(str(path)) + twic_crosstables_pre.read_file(str(path))
        stats["raw_tables"] += len(tables)
        for table in tables:
            signature = (
                table.get("ref"),
                normalize_space(table.get("event")),
                len(table.get("players", [])),
                normalize_space(table.get("players", [{}])[0].get("name") if table.get("players") else ""),
            )
            if signature in seen:
                stats["deduped"] += 1
                continue
            seen.add(signature)
            header_text = normalize_space(table.get("header") or table.get("event"))
            parsed_dates = parse_twic_date(header_text)
            match = find_event(table, index, issue, parsed_dates)
            if match:
                table["event_ref"] = match.ref
                table["start"] = iso(match.start)
                table["end"] = iso(match.end)
                table["place"] = match.place
                table["country"] = match.country
                if match.event_type:
                    table["format"] = match.event_type
                if match.cadence:
                    table["cadence"] = match.cadence
                stats["matched_event_ref"] += 1
            elif parsed_dates:
                start, end = parsed_dates
                table["start"] = iso(start)
                table["end"] = iso(end)
                stats["date_from_header"] += 1
            else:
                stats["undated"] += 1
                continue
            table["source"] = "twic"
            table["reader"] = "twic_crosstable_sources/0.1"
            table["rating_system"] = "fide"
            table["url"] = "https://theweekinchess.com"
            table["source_path"] = str(path)
            out.append(table)
            stats["emitted"] += 1
    return out, stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", default=[str(DEFAULT_HTML)])
    ap.add_argument("--events", default=str(DEFAULT_EVENTS))
    ap.add_argument("--out", default=str(DEFAULT_JSON))
    ap.add_argument("--ctml-out", default=str(DEFAULT_CTML))
    ap.add_argument("--no-ctml", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    files = gather_files(args.inputs)
    events = build_event_index(load_events(Path(args.events)))
    tables, stats = read_all(files, events)
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
