#!/usr/bin/env python3
"""Shared splice-in-new-entries logic for registry/events.xml.

Extracted from scripts/seed_event_registry.py (the crosstables-sourced
seeding path) so scripts/pgn_to_ctml.py's --register-new-events option (the
PGN-sourced path) uses the exact same splice mechanism instead of a second,
possibly-drifting reimplementation. Both callers build the same shape of
input (ref, series_ref, name, aliases, event_type, start/end PartialDate,
notes) and get the same append-only, existing-entries-untouched behavior.
"""

from __future__ import annotations

import re
from pathlib import Path

from ctml_source_common import PartialDate, esc


def occurrence_xml(
    ref: str,
    series_ref: str,
    name: str,
    aliases: list[tuple[str, str, str]],
    event_type: str,
    start: PartialDate,
    end: PartialDate,
    notes: list[str],
) -> str:
    lines = [f'  <ctml:eventOccurrence ref="{esc(ref)}">']
    lines.append(f"    <ctml:seriesRef>{esc(series_ref)}</ctml:seriesRef>")
    lines.append(f"    <ctml:name>{esc(name)}</ctml:name>")
    lines.append("    <ctml:aliases>")
    for spelling, source, site in aliases:
        site_attr = f' site="{esc(site)}"' if site else ""
        lines.append(f'      <ctml:alias source="{esc(source)}"{site_attr}>{esc(spelling)}</ctml:alias>')
    lines.append("    </ctml:aliases>")
    if event_type:
        lines.append(f"    <ctml:eventType>{esc(event_type)}</ctml:eventType>")
    lines.append("    <ctml:dates>")
    lines.append(f"      {start.element('start')}")
    lines.append(f"      {end.element('end')}")
    lines.append("    </ctml:dates>")
    if notes:
        lines.append(f"    <ctml:notes>{esc('; '.join(notes))}</ctml:notes>")
    lines.append("  </ctml:eventOccurrence>")
    return "\n".join(lines)


def series_xml(ref: str, name: str) -> str:
    return f'  <ctml:eventSeries ref="{esc(ref)}">\n    <ctml:name>{esc(name)}</ctml:name>\n  </ctml:eventSeries>'


def existing_refs(registry_text: str) -> tuple[set[str], set[str]]:
    """Returns (existing_series_refs, existing_occurrence_refs)."""
    series = set(re.findall(r'<ctml:eventSeries ref="([^"]+)"', registry_text))
    occurrences = set(re.findall(r'<ctml:eventOccurrence ref="([^"]+)"', registry_text))
    return series, occurrences


def splice_registry(registry_path: Path, new_series: dict[str, str], new_occurrence_blocks: list[str]) -> None:
    """Appends new series/occurrence blocks in place. Schema order is all
    series, then all occurrences -- series are spliced before the first
    existing occurrence (or before the closing tag if there are none yet),
    occurrences before the closing tag."""
    if not new_series and not new_occurrence_blocks:
        return
    registry = registry_path.read_text(encoding="utf-8")
    if new_series:
        series_blocks = [series_xml(ref, name) for ref, name in sorted(new_series.items())]
        first_occurrence = registry.find("  <ctml:eventOccurrence")
        insert_at = first_occurrence if first_occurrence != -1 else registry.find("</ctml:eventRegistry>")
        registry = registry[:insert_at] + "\n".join(series_blocks) + "\n\n" + registry[insert_at:]
    if new_occurrence_blocks:
        closing = registry.find("</ctml:eventRegistry>")
        registry = registry[:closing] + "\n".join(new_occurrence_blocks) + "\n\n" + registry[closing:]
    registry_path.write_text(registry, encoding="utf-8")
