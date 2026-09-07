"""Regenerate corpus.json -- the manifest the web viewer reads to populate its
picker. Scans tours/*.ctml, extracts a summary of each, and writes corpus.json
at the repo root. Run after adding or rebuilding any tournament file.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NS = "urn:ctml:2.0"


def q(local: str) -> str:
    return f"{{{NS}}}{local}"


def text(el, path):
    found = el.find(path)
    return found.text if found is not None else None


def iso_of(day_or_month) -> str | None:
    if day_or_month is None:
        return None
    d = day_or_month.find(q("day"))
    if d is not None:
        return d.get("iso") or f"{d.get('y')}-{int(d.get('m')):02d}-{int(d.get('d')):02d}"
    m = day_or_month.find(q("month"))
    if m is not None:
        return f"{m.get('y')}-{int(m.get('m')):02d}"
    y = day_or_month.find(q("year"))
    if y is not None:
        return str(y.get("y"))
    return None


def derived_category(value: int) -> int:
    """FIDE norm-category band from an Elo-scale value: cat 1 = 2251-2275,
    cat 14 = 2576-2600, and so on in 25-point bands starting at 2251.
    Returns 0 below 2251 (no category). Applied as a fallback for any
    Elo-scale averageRating whose CTML omits @category, including non-FIDE
    systems (edo, chessmetrics) — the band is a mathematical property of the
    value, not a claim that the event awarded FIDE norms."""
    if value < 2251:
        return 0
    return (value - 2251) // 25 + 1


def strength_entries(header) -> list[dict]:
    """One dict per header/averageRating: system, scope, value (int), category (int)."""
    out: list[dict] = []
    if header is None:
        return out
    for el in header.findall(q("averageRating")):
        try:
            value = int((el.text or "").strip())
        except (TypeError, ValueError):
            continue
        cat_raw = el.get("category")
        if cat_raw is None:
            category = derived_category(value)
        else:
            try:
                category = int(cat_raw)
            except ValueError:
                category = derived_category(value)
        out.append({
            "system": el.get("system") or "fide",
            "scope": el.get("scope") or "standard",
            "value": value,
            "category": category,
        })
    return out


def summarize(path: Path) -> dict:
    root = ET.parse(path).getroot()
    header = root.find(q("header"))
    dates = header.find(q("dates")) if header is not None else None
    event_types = [e.text for e in header.findall(q("eventType"))] if header is not None else []
    teams = root.find(q("teams"))
    groups = root.find(q("groups"))
    bracket = root.find(q("bracket"))
    participants = root.find(q("participants"))
    games = root.find(q("games"))
    strength = strength_entries(header)
    # Headline figure for the corpus table: the fide/standard entry, if there is
    # one, else the first fide entry (usually rapid), else the first entry of any
    # system. The list stays in the record too so a viewer can render all bands.
    headline = next((s for s in strength if s["system"] == "fide" and s["scope"] == "standard"),
                    next((s for s in strength if s["system"] == "fide"),
                         strength[0] if strength else None))
    return {
        "file": f"tours/{path.name}",
        "id": root.get("id"),
        "name": text(header, q("name")) if header is not None else path.stem,
        "eventType": event_types,
        "cadence": text(header, q("cadence")) if header is not None else None,
        "federation": text(header, q("federation")) if header is not None else None,
        "place": text(header, f"{q('placeRef')}/{q('name')}") if header is not None else None,
        "start": iso_of(dates.find(q("start"))) if dates is not None else None,
        "end": iso_of(dates.find(q("end"))) if dates is not None else None,
        "participants": len(participants.findall(q("participant"))) if participants is not None else 0,
        "teams": len(teams.findall(q("team"))) if teams is not None else 0,
        "games": len(games.findall(q("game"))) if games is not None else 0,
        "hasBracket": bracket is not None,
        "hasGroups": groups is not None,
        "bytes": path.stat().st_size,
        "strength": strength,
        "avgRating": headline["value"] if headline else None,
        "fideCategory": headline["category"] if headline and headline["category"] else None,
    }


def main() -> None:
    entries = [summarize(p) for p in sorted((ROOT / "tours").glob("*.ctml"))]
    entries.sort(key=lambda e: (e["start"] or ""), reverse=True)
    (ROOT / "corpus.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote corpus.json with {len(entries)} tournaments")
    for e in entries:
        print(f"  {e['start']}  {e['name']}  ({e['participants']}p, {e['games']}g)")


if __name__ == "__main__":
    main()
