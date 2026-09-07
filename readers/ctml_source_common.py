#!/usr/bin/env python3
"""Shared helpers for crosstable source readers and CTML draft emission.

Ported from D:\\ctml\\readers\\ctml_source_common.py (CTML 1.3) to target the
new CTML 2.0 schema set in xsd/. The only structural change needed was
PartialDate's XML output: v2's PartialDateType is a choice of
year/month/day precision elements (see xsd/ctml-dates.xsd) instead of a
single flat attribute bag with a @precision attribute, because XSD 1.0
cannot express v1's conditional-required-attributes assert. Everything else
in this file -- name structuring, score/rating parsing, HTML table
extraction, slugging, the crosstable_to_ctml draft emitter -- carried over
unchanged; none of it is schema-shape-dependent.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from xml.sax.saxutils import escape as xml_escape


CTML_NS = "urn:ctml:2.0"
CTML_VERSION = "2.0"
TITLE_VALUES = {"GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM"}
EVENT_FORMATS = {"round-robin", "swiss", "match", "team", "knockout", "scheveningen", "other", "unknown"}
CADENCES = {"classical", "rapid", "blitz", "bullet", "correspondence", "mixed", "unknown"}
RATING_SYSTEMS = {"fide", "uscf", "ecf", "national", "online", "edo", "chessmetrics", "combined", "none", "unknown"}
MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
SUFFIXES = {"jr", "jr.", "sr", "sr.", "i", "ii", "iii", "iv", "v", "vi", "2nd", "3rd"}


@dataclass(frozen=True)
class PartialDate:
    y: int
    m: int | None = None
    d: int | None = None

    @property
    def precision(self) -> str:
        if self.m is not None and self.d is not None:
            return "day"
        if self.m is not None:
            return "month"
        return "year"

    def is_calendar_day(self) -> bool:
        if self.m is None or self.d is None:
            return False
        leap = self.y % 4 == 0 and (self.y % 100 != 0 or self.y % 400 == 0)
        days = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        return 1 <= self.m <= 12 and 1 <= self.d <= days[self.m]

    def _inner_attrs(self) -> str:
        # CTML 2.0 shape: only the attributes valid at this precision level
        # (see YearPrecisionDateType/MonthPrecisionDateType/DayPrecisionDateType
        # in xsd/ctml-dates.xsd). No @precision attribute -- the wrapping
        # element name (self.precision) carries that.
        attrs = [f'y="{self.y}"']
        if self.m is not None:
            attrs.append(f'm="{self.m}"')
        if self.d is not None:
            attrs.append(f'd="{self.d}"')
        if self.is_calendar_day():
            attrs.append(f'iso="{self.y:04}-{self.m:02}-{self.d:02}"')
        return " ".join(attrs)

    def element(self, tag: str) -> str:
        return f"<ctml:{tag}><ctml:{self.precision} {self._inner_attrs()}/></ctml:{tag}>"

    def compact(self) -> str:
        return f"{self.y:04}{self.m or 0:02}{self.d or 0:02}"


class SimpleTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._in_table = 0
        self._rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._rows = []
        elif tag == "tr" and self._in_table == 1:
            self._row = []
        elif tag in {"td", "th"} and self._in_table == 1 and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(normalize_space("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._in_table == 1:
            if any(c for c in self._row):
                self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._in_table:
            if self._in_table == 1 and self._rows:
                self.tables.append(self._rows)
            self._in_table -= 1


def normalize_space(value: Any) -> str:
    return " ".join(html.unescape("" if value is None else str(value)).replace("\xa0", " ").split())


def esc(value: Any) -> str:
    return xml_escape("" if value is None else str(value), {'"': "&quot;"})


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "windows-1252", "iso-8859-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def extract_tables(text: str) -> list[list[list[str]]]:
    parser = SimpleTableParser()
    parser.feed(text)
    return parser.tables


def sha1_hex16(data: str) -> str:
    return hashlib.sha1(data.encode("utf-8")).hexdigest()[:16]


def synth_player_ref(name: str, fed: str) -> str:
    base = f"{name.strip().lower()}|{fed.strip().upper()}"
    return f"player:syn:{sha1_hex16(base)}"


def place_raw_ref(site: str) -> str:
    return f"place:raw:{sha1_hex16(site.strip().lower())}"


def place_city_ref(city: str, country: str = "") -> str:
    return f"place:city:{sha1_hex16(f'{city.strip().lower()}|{country.strip().lower()}')}"


def slug(value: str, fallback: str = "unknown") -> str:
    text = normalize_space(value).lower()
    out: list[str] = []
    pending_dash = False
    for ch in text:
        if ch.isalnum():
            if pending_dash and out:
                out.append("-")
            pending_dash = False
            out.append(ch)
        elif ch.isspace() or ch in "_-":
            pending_dash = True
    return "".join(out) or fallback


def xml_id(*parts: str) -> str:
    raw = "_".join(p for p in parts if p)
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_.-")
    if not cleaned or not re.match(r"[A-Za-z_]", cleaned):
        cleaned = f"t_{cleaned or sha1_hex16(raw)}"
    return cleaned[:180]


def to_int(value: Any) -> int | None:
    text = normalize_space(value)
    if not re.fullmatch(r"\d+", text):
        return None
    return int(text)


def parse_points(value: Any) -> float | None:
    text = normalize_space(value)
    if not text:
        return None
    text = (
        text.replace("½", ".5")
        .replace("¼", ".25")
        .replace("¾", ".75")
        .replace(",", ".")
        .replace(" ", "")
    )
    text = re.sub(r"^(\d+)\+?$", r"\1", text)
    if re.fullmatch(r"\d+/\d+", text):
        a, b = text.split("/", 1)
        return float(a) / float(b)
    if re.fullmatch(r"\d+\.\d+|\d+", text):
        return float(text)
    return None


def split_score_text(value: Any) -> str:
    score = parse_points(value)
    return f"{score:g}" if score is not None else normalize_space(value)


def normalize_fed(value: Any) -> str:
    text = normalize_space(value).upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else ""


def normalize_title(value: Any) -> tuple[str, str]:
    raw = normalize_space(value).upper()
    raw = {
        "G": "GM",
        "M": "IM",
        "F": "FM",
        "C": "CM",
        "N": "NM",
    }.get(raw, raw)
    if raw in TITLE_VALUES:
        return raw, ""
    return "", raw


def infer_cadence(*texts: str) -> str | None:
    haystack = " ".join(texts).lower()
    if re.search(r"\b(blitz|блиц)\b", haystack):
        return "blitz"
    if re.search(r"\b(rapid|рапид|rapide)\b", haystack):
        return "rapid"
    if re.search(r"\bbullet\b", haystack):
        return "bullet"
    if re.search(r"\b(classical|классика|standard)\b", haystack):
        return "classical"
    return None


def map_event_format(value: Any) -> str:
    text = normalize_space(value).lower()
    if text in EVENT_FORMATS:
        return text
    if "schweizer" in text or "swiss" in text:
        return "swiss"
    if "rundenturnier" in text or "round robin" in text or "round-robin" in text:
        return "round-robin"
    if "match" in text:
        return "match"
    if "team" in text:
        return "team"
    return "unknown"


def parse_yyyymmdd(value: Any) -> PartialDate | None:
    text = normalize_space(value)
    if not re.fullmatch(r"\d{8}", text):
        return None
    y, m, d = int(text[:4]), int(text[4:6]), int(text[6:8])
    if m == 0:
        return PartialDate(y)
    if d == 0:
        return PartialDate(y, m)
    return PartialDate(y, m, d)


def parse_iso_date(value: Any) -> PartialDate | None:
    text = normalize_space(value)
    match = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
    if not match:
        return parse_yyyymmdd(text)
    y = int(match.group(1))
    m = int(match.group(2)) if match.group(2) else None
    d = int(match.group(3)) if match.group(3) else None
    return PartialDate(y, m, d)


def month_number(token: str) -> int | None:
    key = re.sub(r"[^A-Za-z]", "", token).lower()
    return MONTHS.get(key) or MONTHS.get(key[:3])


def parse_date_part(part: str) -> tuple[int | None, int | None]:
    tokens = [t for t in re.split(r"[\s,.]+", part.strip()) if t]
    day = None
    month = None
    for token in tokens:
        if month is None:
            month = month_number(token)
            if month is not None:
                continue
        if day is None and re.fullmatch(r"\d{1,2}", token):
            n = int(token)
            if 1 <= n <= 31:
                day = n
    return month, day


def parse_text_date_range(text: str, fallback_year: int | None = None) -> tuple[PartialDate, PartialDate] | None:
    raw = normalize_space(text)
    if not raw:
        return None
    raw = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", raw, flags=re.I)
    raw = raw.replace("–", "-").replace("—", "-")
    years = [int(y) for y in re.findall(r"\b(1[5-9]\d{2}|20\d{2})\b", raw)]
    year = years[-1] if years else fallback_year
    if not year:
        return None
    no_year = re.sub(r"\b(1[5-9]\d{2}|20\d{2})\b", "", raw).strip(" ,")
    if not no_year:
        return PartialDate(year), PartialDate(year)
    parts = [p.strip(" ,") for p in re.split(r"\s+(?:-|to|until|through)\s+|\s*-\s*", no_year, maxsplit=1, flags=re.I)]
    if len(parts) == 1:
        m, d = parse_date_part(parts[0])
        date = PartialDate(year, m, d) if m else PartialDate(year)
        return date, date
    start_m, start_d = parse_date_part(parts[0])
    end_m, end_d = parse_date_part(parts[1])
    if start_m is None:
        start_m = end_m
    if end_m is None:
        end_m = start_m
    start = PartialDate(year, start_m, start_d) if start_m else PartialDate(year)
    end = PartialDate(year, end_m, end_d) if end_m else PartialDate(year)
    return start, end


def person_name_xml(raw: str, indent: str) -> str:
    raw = normalize_space(raw) or "Unknown"
    lines = [f'{indent}<ctml:name display="{esc(raw)}">']
    if "," in raw:
        family, rest = raw.split(",", 1)
        family = family.strip()
        tokens = rest.strip().split()
        suffix = None
        if tokens and tokens[-1].lower() in SUFFIXES:
            suffix = tokens.pop()
        lines.append(f"{indent}  <ctml:family>{esc(family)}</ctml:family>")
        for given in tokens:
            lines.append(f"{indent}  <ctml:given>{esc(given)}</ctml:given>")
        if suffix:
            lines.append(f"{indent}  <ctml:suffix>{esc(suffix)}</ctml:suffix>")
    else:
        tokens = raw.split()
        if len(tokens) == 1:
            lines.append(f"{indent}  <ctml:family>{esc(tokens[0])}</ctml:family>")
        elif tokens:
            # PersonNameType requires family before given (xs:sequence) --
            # last token is treated as the family name, but must still be
            # emitted first. (Bug found and fixed 2026-07-31: the original
            # D:\ctml version of this function had given before family here,
            # which is invalid against ctml-names.xsd/ctml-namedate.xsd in
            # both v1 and v2. It slipped through undetected in the crosstable
            # pipeline because TWIC/OlimpBase names are almost always
            # comma-form, which took the other branch.)
            lines.append(f"{indent}  <ctml:family>{esc(tokens[-1])}</ctml:family>")
            for given in tokens[:-1]:
                lines.append(f"{indent}  <ctml:given>{esc(given)}</ctml:given>")
        else:
            lines.append(f"{indent}  <ctml:unstructured>Unknown</ctml:unstructured>")
    lines.append(f"{indent}</ctml:name>")
    return "\n".join(lines)


def xlsx_rows(path: Path) -> list[list[str]]:
    ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("a:si", ns):
                shared.append("".join(t.text or "" for t in si.findall(".//a:t", ns)))
        root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        out: list[list[str]] = []
        for row in root.findall("a:sheetData/a:row", ns):
            cells: dict[int, str] = {}
            max_col = 0
            for cell in row.findall("a:c", ns):
                ref = cell.attrib.get("r", "")
                match = re.match(r"([A-Z]+)", ref)
                if not match:
                    continue
                col = 0
                for ch in match.group(1):
                    col = col * 26 + ord(ch) - ord("A") + 1
                max_col = max(max_col, col)
                value = cell.findtext("a:v", default="", namespaces=ns)
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(t.text or "" for t in cell.findall(".//a:t", ns))
                cells[col] = normalize_space(value)
            out.append([cells.get(i, "") for i in range(1, max_col + 1)])
        return out


def player_ref_for(player: dict[str, Any]) -> tuple[str, str]:
    explicit = normalize_space(player.get("ref"))
    if explicit:
        if explicit.startswith("player:syn:"):
            return explicit, "unresolved"
        return explicit, ""
    fide_id = normalize_space(player.get("fide_id"))
    if re.fullmatch(r"\d{4,12}", fide_id):
        return f"player:fide:{fide_id}", "fide-id"
    return synth_player_ref(player.get("name", ""), player.get("fed", "")), "unresolved"


def crosstable_to_ctml(table: dict[str, Any]) -> str | None:
    event = normalize_space(table.get("event"))
    players = [p for p in table.get("players", []) if normalize_space(p.get("name"))]
    if not event or not players:
        return None

    start = parse_iso_date(table.get("start")) if table.get("start") else None
    end = parse_iso_date(table.get("end")) if table.get("end") else None
    if not start:
        return None
    if not end:
        end = start

    source = normalize_space(table.get("source") or "source")
    ref = normalize_space(table.get("ref") or sha1_hex16(json.dumps(table, ensure_ascii=False, sort_keys=True)))
    tid = xml_id("t", source.replace("-", "_"), slug(ref), start.compact())
    fmt = map_event_format(table.get("format"))
    cadence = normalize_space(table.get("cadence"))
    if cadence not in CADENCES:
        cadence = infer_cadence(event, table.get("notes", "")) or ""
    rating_system = normalize_space(table.get("rating_system") or "unknown")
    if rating_system not in RATING_SYSTEMS:
        rating_system = "unknown"

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<ctml:tournament xmlns:ctml="{CTML_NS}" ctmlVersion="{CTML_VERSION}" id="{esc(tid)}">',
        "  <ctml:header>",
        f"    <ctml:name>{esc(event)}</ctml:name>",
    ]
    event_ref = normalize_space(table.get("event_ref"))
    if event_ref:
        lines.extend(
            [
                f'    <ctml:eventRef ref="{esc(event_ref)}">',
                f"      <ctml:name>{esc(event)}</ctml:name>",
                "    </ctml:eventRef>",
            ]
        )
    if fmt in EVENT_FORMATS and fmt != "unknown":
        lines.append(f"    <ctml:eventType>{fmt}</ctml:eventType>")
    if cadence:
        lines.append(f"    <ctml:cadence>{cadence}</ctml:cadence>")
    lines.extend(["    <ctml:dates>", f"      {start.element('start')}", f"      {end.element('end')}", "    </ctml:dates>"])

    place = normalize_space(table.get("place"))
    country = normalize_fed(table.get("country"))
    if place:
        kind = "country" if country and place == country else "unknown"
        pref = place_raw_ref(place)
        lines.append(f'    <ctml:placeRef ref="{esc(pref)}" kind="{kind}">')
        lines.append(f"      <ctml:name>{esc(place)}</ctml:name>")
        if country:
            lines.append(f"      <ctml:country>{country}</ctml:country>")
        lines.append("    </ctml:placeRef>")
    lines.append("  </ctml:header>")

    lines.append("  <ctml:participants>")
    for idx, player in enumerate(players, start=1):
        pid = f"p{idx:04}"
        name = normalize_space(player.get("name")) or "Unknown"
        fed = normalize_fed(player.get("fed"))
        title, raw_title = normalize_title(player.get("title"))
        player_ref, method = player_ref_for({"name": name, "fed": fed, "fide_id": player.get("fide_id")})
        lines.append(f'    <ctml:participant id="{pid}">')
        source_url = normalize_space(table.get("url"))
        source_attr = f' source="{esc(source_url)}"' if source_url else ""
        lines.append(f'      <ctml:playerRef ref="{esc(player_ref)}"{source_attr}>')
        lines.append(person_name_xml(name, "        "))
        if fed:
            lines.append(f"        <ctml:federation>{fed}</ctml:federation>")
        if title:
            lines.append(f"        <ctml:title>{title}</ctml:title>")
        fide_id = normalize_space(player.get("fide_id"))
        internal_id = normalize_space(player.get("source_id") or player.get("seed"))
        if re.fullmatch(r"\d{4,12}", fide_id) or internal_id:
            lines.append("        <ctml:ids>")
            if re.fullmatch(r"\d{4,12}", fide_id):
                lines.append(f"          <ctml:fideId>{fide_id}</ctml:fideId>")
            if internal_id:
                lines.append(f"          <ctml:internalId>{esc(source)}:{esc(internal_id)}</ctml:internalId>")
            lines.append("        </ctml:ids>")
        if method == "unresolved":
            lines.append(f'        <ctml:resolution method="unresolved" resolver="{esc(table.get("reader") or source)}"/>')
        lines.append("      </ctml:playerRef>")
        rating = to_int(player.get("rating"))
        if rating is not None:
            lines.extend(
                [
                    f'      <ctml:ratingSnapshot system="{rating_system}" scope="{normalize_space(table.get("rating_scope") or "standard")}">',
                    f"        <ctml:value>{rating}</ctml:value>",
                    f"        {start.element('asOf')}",
                    "      </ctml:ratingSnapshot>",
                ]
            )
        seed = to_int(player.get("seed"))
        if seed is not None:
            lines.append(f"      <ctml:seed>{seed}</ctml:seed>")
        score = parse_points(player.get("score"))
        if score is not None:
            lines.append(f"      <ctml:score>{score:g}</ctml:score>")
        notes = []
        rank = to_int(player.get("rank"))
        if rank is not None:
            notes.append(f"rank={rank}")
        if raw_title:
            notes.append(f"raw_title={raw_title}")
        for key in ("team", "club", "sex", "age", "group"):
            value = normalize_space(player.get(key))
            if value:
                notes.append(f"{key}={value}")
        if notes:
            lines.append(f"      <ctml:notes>{esc('; '.join(notes))}</ctml:notes>")
        lines.append("    </ctml:participant>")
    lines.append("  </ctml:participants>")

    notes = [normalize_space(table.get("notes"))]
    if normalize_space(table.get("classification")) == "team":
        notes.append("Source table was classified as team standings and should be reviewed before publication.")
    notes = [n for n in notes if n]
    if notes:
        lines.append(f"  <ctml:notes>{esc(' '.join(notes))}</ctml:notes>")

    source_path = normalize_space(table.get("source_path"))
    source_url = normalize_space(table.get("url"))
    lines.append(f'  <ctml:source kind="{esc(source)}">')
    if source_url:
        lines.append(f"    <ctml:uri>{esc(source_url)}</ctml:uri>")
    source_note = f"ref={ref}"
    if source_path:
        source_note += f"; source_path={source_path}"
    lines.append(f"    <ctml:note>{esc(source_note)}</ctml:note>")
    lines.append("  </ctml:source>")
    lines.append("</ctml:tournament>")
    lines.append("")
    return "\n".join(lines)


def write_ctml_files(tables: Iterable[dict[str, Any]], out_dir: Path, clear: bool = False) -> tuple[int, int]:
    if clear and out_dir.exists():
        for path in out_dir.glob("*.xml"):
            path.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    seen: dict[str, int] = {}
    for table in tables:
        xml = crosstable_to_ctml(table)
        if xml is None:
            skipped += 1
            continue
        source = normalize_space(table.get("source") or "source")
        ref = normalize_space(table.get("ref") or str(written + 1))
        event = normalize_space(table.get("event") or "event")
        base = f"{slug(ref)}-{slug(event)}"
        prefix = normalize_space(table.get("_filename_prefix"))
        if prefix:
            base = f"{slug(prefix)}-{base}"
        seen[base] = seen.get(base, 0) + 1
        suffix = f"-{seen[base]}" if seen[base] > 1 else ""
        (out_dir / f"{base[:150]}{suffix}.xml").write_text(xml, encoding="utf-8")
        written += 1
    return written, skipped


def write_json(data: Any, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
