"""Read crosstables out of a TWIC HTML issue into the crosstable contract.

Handles the modern HTML era (`<table class="results-table" ... Crosstable>`),
which covers the great majority of issues in both its sub-styles:

  - round-robin: a result grid (* . 1/2 1 0) with a total at the end;
  - Swiss:       a standings table with a "Pts" column.

The classic fixed-width <pre> era (issues 1-683) is handled by its sibling
twic_crosstables_pre.py — a separate small reader, which is exactly the point
of the per-source design. Both emit the same crosstable contract.

Usage:
    python twic_crosstables.py <twic*.html> [more files...] > crosstables.json
    python twic_crosstables.py --summary <twic*.html> [more files...]
"""

import html
import json
import os
import re
import sys


# Chess titles as TWIC writes them (uppercase "GM"/"WIM" and the lowercase
# figurine forms "g"/"m"/"w"), used to tell a title column from a federation
# or rating when a table has no header to name its columns.
TITLES = {
    "gm", "im", "fm", "cm", "nm", "wgm", "wim", "wfm", "wcm", "wnm",
    "g", "m", "f", "c", "w", "n", "h",
}


def strip_tags(fragment):
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def cells(row):
    """(tag, text) for each <th>/<td> in a row. Cells never nest, so the
    nearest </th>|</td> closes each one."""
    return [
        (tag.lower(), " ".join(strip_tags(inner).split()))
        for tag, inner in re.findall(r"<(t[hd])\b[^>]*>(.*?)</t[hd]>", row, re.S | re.I)
    ]


def issue_number(htmltext):
    m = re.search(r"THE WEEK IN CHESS\s+(\d+)", htmltext, re.I)
    return int(m.group(1)) if m else None


def parse_points(cell):
    """A points value, which may use the ½ glyph: "3½" -> 3.5, "4" -> 4.0."""
    c = cell.strip().replace("½", ".5")
    if c.startswith("."):
        c = "0" + c
    try:
        return float(c)
    except ValueError:
        return None


def parse_score(texts, labels, is_round_robin):
    """The final-points value. A standings table carries a "Pts" column. A
    round-robin grid has none, so anchor on the trailing performance rating —
    a big number (>=1000) — whose immediately preceding cell is the total
    (which may be written with a ½)."""
    if labels:
        low = [x.lower() for x in labels]
        for key in ("pts", "total", "score", "points"):
            if key in low and low.index(key) < len(texts):
                return parse_points(texts[low.index(key)])
    if not is_round_robin:
        return None
    for i in range(len(texts) - 1, 5, -1):  # rightmost performance rating
        if re.fullmatch(r"\d{3,4}", texts[i]) and int(texts[i]) >= 1000:
            return parse_points(texts[i - 1])
    return None


def to_number(text, cast):
    try:
        return cast(text)
    except (ValueError, TypeError):
        return None


def split_rows(body):
    """Split a table body into rows on the `<tr` starts. TWIC often omits the
    closing </tr>, so we can't rely on it; the next <tr (or end) bounds a row.
    """
    return ["<tr" + chunk.split("</tr>", 1)[0] for chunk in re.split(r"<tr\b", body, flags=re.I)[1:]]


def parse_crosstable(body):
    rows = split_rows(body)
    event = None
    header_text = ""
    labels = None
    raw_players = []

    for row in rows:
        cs = cells(row)
        if not cs:
            continue
        tags = {t for t, _ in cs}
        texts = [x for _, x in cs]
        if tags == {"th"}:
            if event is None:
                bold = re.search(r"<(?:b|strong)>(.*?)</(?:b|strong)>", row, re.S | re.I)
                event = strip_tags(bold.group(1)) if bold else texts[0]
                header_text = " ".join(strip_tags(row).split())
            else:
                labels = texts
            continue
        # A data row: first cell is the rank.
        rank = texts[0].rstrip(".")
        if rank.isdigit():
            raw_players.append(texts)

    if not event or not raw_players:
        return None

    is_round_robin = any("*" in texts for texts in raw_players)

    # Two ways to place columns:
    #  - by header label when the table names them ("Rk Name Ti FED Rtg Pts");
    #    this also handles Swiss tables that OMIT Title or Fed columns;
    #  - otherwise (round-robin grids, team standings, older layouts) there is
    #    no named header, so identify columns by content: a fed is a 3-letter
    #    code, a rating a 4-digit number, a title a known code. A team table
    #    has none of these, so those fields correctly come out empty.
    low = [x.lower() for x in labels] if labels else []

    def col(names, default):
        for name in names:
            if name in low:
                return low.index(name)
        return default

    named = any(k in low for k in ("name", "rk", "rtg", "rating", "pts"))
    if named:
        i_rank = col(("rk", "rank", "no"), 0)
        i_name = col(("name", "player"), 1)
        i_title = col(("ti", "title"), None)  # None => column absent
        i_fed = col(("fed", "federation", "country", "nat"), None)
        i_rtg = col(("rtg", "rating", "elo"), None)

    def cell(texts, idx):
        return texts[idx] if idx is not None and 0 <= idx < len(texts) else ""

    def by_content(texts):
        title = texts[2] if len(texts) > 2 and texts[2].lower() in TITLES else ""
        fed = next((c for c in texts[2:6] if re.fullmatch(r"[A-Za-z]{3}", c) and c.lower() not in TITLES), "")
        rtg = next((c for c in texts[2:6] if c.isdigit() and 1000 <= int(c) <= 2999), "")
        return title, fed, rtg

    players = []
    for texts in raw_players:
        if named:
            rank, name = cell(texts, i_rank), cell(texts, i_name)
            title, fed, rtg = cell(texts, i_title), cell(texts, i_fed), cell(texts, i_rtg)
        else:
            rank = texts[0] if texts else ""
            name = texts[1] if len(texts) > 1 else ""
            title, fed, rtg = by_content(texts)
        players.append(
            {
                "rank": to_number(rank.rstrip("."), int),
                "name": name,
                "title": title,
                "fed": fed,
                "rating": to_number(rtg, int) if rtg.isdigit() else None,
                "score": parse_score(texts, labels, is_round_robin),
            }
        )

    return {
        "event": event,
        "header": header_text,
        "format": "round-robin" if is_round_robin else "swiss",
        "players": players,
    }


def read_file(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    issue = issue_number(text)
    out = []
    for m in re.finditer(r"<table\b([^>]*)>(.*?)</table>", text, re.S | re.I):
        attrs, body = m.group(1), m.group(2)
        if "results-table" not in attrs.lower() or "crosstable" not in attrs.lower():
            continue
        table = parse_crosstable(body)
        if table:
            table["source"] = "twic"
            table["ref"] = f"twic-{issue}" if issue else os.path.basename(path)
            out.append(table)
    return out


def gather(args):
    """Expand any directory argument to its .html files, sorted."""
    files = []
    for a in args:
        if os.path.isdir(a):
            files.extend(sorted(
                os.path.join(a, n) for n in os.listdir(a) if n.lower().endswith((".html", ".htm"))
            ))
        else:
            files.append(a)
    return files


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    out_path = None
    if "--out" in args:
        i = args.index("--out")
        out_path = args[i + 1]
        del args[i:i + 2]
    summary = "--summary" in args
    files = gather([a for a in args if not a.startswith("--")])
    if not files:
        print(__doc__)
        return

    all_tables = []
    issues_with = 0
    for path in files:
        tables = read_file(path)
        if tables:
            issues_with += 1
        all_tables.extend(tables)

    players = sum(len(t["players"]) for t in all_tables)
    named = sum(1 for t in all_tables for p in t["players"] if p["name"])
    scored = sum(1 for t in all_tables for p in t["players"] if p["score"] is not None)
    # Grand totals to stderr so stdout stays pure JSON when piped.
    print(
        f"{len(files)} files ({issues_with} with crosstables) -> "
        f"{len(all_tables)} crosstables, {players} players "
        f"(names {named}, scores {scored})",
        file=sys.stderr,
    )

    if summary:
        return
    text = json.dumps(all_tables, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
