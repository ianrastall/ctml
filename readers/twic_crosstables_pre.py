"""Read crosstables out of the classic <pre> TWIC era (issues 1-683).

Where the modern reader (twic_crosstables.py) handles `<table>` crosstables,
this one handles the fixed-width plain-text standings tables that live inside
`<pre>` blocks. They are not one format but a long tail of layouts; what they
share is fixed-width alignment, so columns are found by character offset, not
by HTML cells.

Two column-placement strategies, matching the modern reader:

  - Header path: most tables carry a header line ("Place Name Feder Rtg Score
    Berg.", "Rank Name FED Pts", "No Name Fed ELO Nat # Points", ...). Its
    whitespace-delimited tokens mark the columns; we cut each data row at the
    MIDPOINT of every inter-token gap, which tolerates left/right alignment
    drift and keeps single-spaced adjacent columns ("2550 0" = Rtg + Loc)
    apart. Header labels map to contract fields.

  - Headerless path: round-robin grids and a few bare standings have data rows
    but no named header. There we detect columns by content: a fed is a
    3-letter code, a rating a 4-digit number, a title a known code, the score
    the trailing points value, the name the text run after the rank.

Emits the same crosstable contract as the modern reader (see
crosstable-format.md).

Usage:
    python twic_crosstables_pre.py <twic*.html> [more...|dir] [--out FILE]
    python twic_crosstables_pre.py --summary <dir>
"""

import html
import json
import os
import re
import sys


# Titles as TWIC writes them, upper and figurine forms (see modern reader).
TITLES = {
    "gm", "im", "fm", "cm", "nm", "wgm", "wim", "wfm", "wcm", "wnm",
    "g", "m", "f", "c", "w", "n", "h",
}

# Header tokens (lowercased, trailing '.' stripped) -> contract field. Only the
# first token that maps to a given field wins, so "Rtg ... Loc" keeps Rtg as the
# rating and ignores the local/second rating. Unmapped columns (loc, berg,
# buch, progr, tb1, %, games, rp, ...) are simply dropped.
RANK_LABELS = {"place", "rank", "rk", "no", "bo", "pl", "#", "nr", "pos"}
NAME_LABELS = {"name", "player"}
TITLE_LABELS = {"title", "ttl", "ti"}
FED_LABELS = {"fed", "feder", "nat", "country", "countr", "cou", "fide"}
RATING_LABELS = {"rtg", "rating", "elo", "uscf", "rat", "rtng"}
SCORE_LABELS = {"score", "pts", "points", "total", "pnts"}

# Generic block headings that are not the event name; skip past them when
# walking up to find the tournament title.
GENERIC = re.compile(
    r"^(final\s+)?(standings?|results?|cross\s*table|crosstable|pairings?|"
    r"table|placings?|final|standing)\s*[:.]?\s*$",
    re.I,
)
SEPARATOR = re.compile(r"^[\s\-=_*~.]+$")


def pre_blocks(htmltext):
    """The text of every <pre> block, tags stripped, entities decoded, spacing
    preserved (so fixed-width columns still line up)."""
    return [
        html.unescape(re.sub(r"<[^>]+>", "", body))
        for body in re.findall(r"<pre\b[^>]*>(.*?)</pre>", htmltext, re.S | re.I)
    ]


def issue_number(htmltext):
    m = re.search(r"THE WEEK IN CHESS\s+(\d+)", htmltext, re.I)
    return int(m.group(1)) if m else None


def parse_points(cell):
    """Points value: "7.5" / "7,0" (European comma) / "3½" -> float, else None."""
    c = cell.strip().replace("½", ".5").replace(",", ".")
    if c.startswith("."):
        c = "0" + c
    if not re.fullmatch(r"\d+(\.\d+)?", c):
        return None
    return float(c)


def to_int(text):
    try:
        return int(text)
    except (ValueError, TypeError):
        return None


def header_columns(line):
    """[(field-or-None, start, end)] for each whitespace-run token in a header
    line, with the raw label kept for the ones we don't map."""
    cols = []
    for m in re.finditer(r"\S+", line):
        tok = m.group(0).lower().rstrip(".")
        if tok in RANK_LABELS:
            field = "rank"
        elif tok in NAME_LABELS:
            field = "name"
        elif tok in TITLE_LABELS:
            field = "title"
        elif tok in FED_LABELS:
            field = "fed"
        elif tok in RATING_LABELS:
            field = "rating"
        elif tok in SCORE_LABELS:
            field = "score"
        else:
            field = None
        cols.append((field, m.start(), m.end()))
    return cols


def is_header(line):
    """A line that names at least two known columns, one of them Name."""
    cols = header_columns(line)
    fields = {f for f, _, _ in cols if f}
    return "name" in fields and len(fields) >= 2, cols


def slice_row(row, cols):
    """Cut a data row into its columns. These tables are left-aligned: each
    header label sits at the LEFT of its (often much wider) column, so a column
    runs from its own token start to the start of the next token. Cutting at the
    gap midpoint would truncate wide names; cutting at the next token start does
    not. Returns a list aligned 1:1 with cols."""
    starts = [c[1] for c in cols]
    bounds = starts + [max(len(row), starts[-1])]
    return [row[bounds[i]:bounds[i + 1]].strip() for i in range(len(cols))]


def first_field(cells, cols, field):
    """The stripped value of the first column mapped to `field`, or ""."""
    for cell, (f, _, _) in zip(cells, cols):
        if f == field:
            return cell
    return ""


def clean_fed(text):
    m = re.search(r"\b([A-Za-z]{3})\b", text)
    return m.group(1).upper() if m and m.group(1).lower() not in TITLES else ""


def clean_rating(text):
    for m in re.finditer(r"\b(\d{4})\b", text):
        if 1000 <= int(m.group(1)) <= 2999:
            return int(m.group(1))
    return None


def split_title_from_name(name):
    """"GM Reynaldo Vera" -> ("GM", "Reynaldo Vera") when a title is glued to
    the front of the name (tables with no Title column)."""
    m = re.match(r"([A-Za-z]{1,3})\s+(.+)", name)
    if m and m.group(1).lower() in TITLES:
        return m.group(1), m.group(2).strip()
    return "", name


def parse_data_row_header(row, cols, prev_rank):
    """One data row under a known header. Returns a player dict, or None if it
    has no name."""
    cells = slice_row(row, cols)
    name = first_field(cells, cols, "name")
    if not name:
        return None

    rank_raw = first_field(cells, cols, "rank")
    rank = to_int(re.match(r"\d+", rank_raw).group()) if re.match(r"\d+", rank_raw) else prev_rank

    title = first_field(cells, cols, "title")
    if not title:
        title, name = split_title_from_name(name)

    fed = clean_fed(first_field(cells, cols, "fed"))
    rating = clean_rating(first_field(cells, cols, "rating"))
    score = parse_points(first_field(cells, cols, "score"))
    if score is not None and score > 30:  # a mislabeled tiebreak, not a total
        score = None
    return {
        "rank": rank,
        "name": name,
        "title": title.strip(),
        "fed": fed,
        "rating": rating,
        "score": score,
    }, rank


GRID_CELL = set("*.=10+-½")  # a round-robin result-grid cell


def find_grid(tokens):
    """(lo, hi) index span of a result grid — a run of >=3 single-char cells —
    or (None, None). The grid separates the rating (before) from the total
    (right after)."""
    lo = run = 0
    start = None
    span = (None, None)
    for idx, t in enumerate(tokens):
        if len(t) == 1 and t in GRID_CELL:
            if start is None:
                start = idx
                run = 0
            run += 1
            if run >= 3:
                span = (start, idx)
        else:
            start = None
    return span


def parse_data_row_content(row, prev_rank):
    """One data row with no header: detect columns by content. Split on runs of
    2+ spaces (fixed-width gaps), keeping single-spaced names intact. Layout is
    rank name [title] [fed] rating [grid] total [perf]."""
    parts = [p for p in re.split(r"\s{2,}", row.strip()) if p]
    if len(parts) < 2:
        return None

    # Rank: a leading number (or tie range "1-3"), which may be its own column
    # OR single-spaced onto the name ("1 Stjazhkin, V"). A blank rank inherits
    # the previous row's (tie-continuation).
    rank = prev_rank
    m = re.match(r"(\d+)(?:\s*-\s*\d+)?[.\)]?(?:\s+(.*))?$", parts[0])
    if m:
        rank = int(m.group(1))
        if m.group(2):
            parts[0] = m.group(2)      # name was glued after the rank
        else:
            parts = parts[1:]          # rank stood in its own column
    if not parts:
        return None

    name = parts[0]
    title, name = split_title_from_name(name)
    if not re.search(r"[A-Za-z]", name):
        return None

    tokens = re.split(r"\s+", " ".join(parts[1:]))
    grid_lo, grid_hi = find_grid(tokens)
    before = tokens[:grid_lo] if grid_lo is not None else tokens

    # A figurine title (g/m/f/w...) often sits after the name, before the fed.
    if not title:
        for t in before:
            if t.rstrip(".").lower() in TITLES:
                title = t.rstrip(".")
                break

    fed = clean_fed(" ".join(before))
    rating, rating_idx = None, -1
    for idx, t in enumerate(before):
        if re.fullmatch(r"\d{4}", t) and 1000 <= int(t) <= 2999:
            rating, rating_idx = int(t), idx
            break

    # Score: the total sits just after the rating (Swiss) or after the result
    # grid (round-robin) — the FIRST points value there, not the last, so
    # trailing perf-rating / rating-change / tiebreak columns are not mistaken
    # for it ("... 2472  4½  3  2189  2.52 -0.02" -> 4.5, not 2.52).
    start = grid_hi + 1 if grid_hi is not None else rating_idx + 1
    score = None
    for t in tokens[start:] if start > 0 else []:
        v = parse_points(t)
        # A real chess score is bounded by the rounds played (<=30 even for a
        # long double round-robin). Skipping bigger values steps over opponent
        # numbers ("+146 -41") and Buchholz/count columns to the true total.
        if v is not None and v <= 30 and not re.fullmatch(r"\d{4}", t):
            score = v
            break

    return {
        "rank": rank,
        "name": name,
        "title": title,
        "fed": fed,
        "rating": rating,
        "score": score,
    }, rank


ROUND_MARKER = re.compile(r"^(final\s+)?(standings?\s+)?rounds?\b|\bround\s+\d", re.I)


def _is_grid_header(s):
    """A result-grid column header ("1 2 3 4 5 6 7 8 9 0 1 2 TOTAL", "PLAYERS
    NAT ELO 1 2 3 ... NORM"): dominated by 1-2 digit round numbers."""
    toks = s.split()
    if len(toks) < 4:
        return False
    small = sum(1 for t in toks if re.fullmatch(r"\d{1,2}", t))
    return small >= 4 and small >= len(toks) // 3


def _is_label_header(s):
    """A header line, whether or not it names a "Name" column — three or more of
    its tokens map to known fields ("T Nat Elo Code Pts ...")."""
    return sum(1 for f, _, _ in header_columns(s) if f) >= 3


def _is_data_line(line):
    """A standings/pairing data row (so NOT an event title): leads with a rank
    then a rating/year, or carries dotted name leaders. Title ordinals ("10th",
    "1st", "XI Open") have no space after their digits, so they don't match."""
    return bool(
        (re.match(r"\s*\d+\.?\s+\S", line) and re.search(r"\b\d{4}\b", line))
        or "...." in line
    )


def event_title(lines, header_idx):
    """Walk up from a table toward its event name. The pre-text is freeform, so
    this skips the many things that are NOT a title — separators, generic
    "Standings" headings, round markers, other header/grid-header lines, and
    trailing-colon labels — and takes the first plausible line within a short
    window. Stops (=> "?") on reaching the previous table's data rather than
    stealing that event's title, and never emits an obvious non-title."""
    examined = 0
    for i in range(header_idx - 1, -1, -1):
        s = lines[i].strip()
        if not s or SEPARATOR.match(s):
            continue
        if _is_data_line(lines[i]):
            return None
        examined += 1
        if examined > 6:
            break
        if (GENERIC.match(s) or ROUND_MARKER.search(s) or s.endswith(":")
                or _is_label_header(s) or _is_grid_header(s)):
            continue
        if len(re.findall(r"[A-Za-z]", s)) < 3:  # mostly punctuation/numbers
            continue
        # Trim a trailing fixed-width column ("... 2007   cat. X (2482)").
        return re.split(r"\s{3,}", s)[0].strip(" .-=\t")
    return None


def data_row_looks_real(line):
    """A candidate standings row: non-blank, not a separator, and either
    rank-led or carrying a 4-digit rating (tie-continuation rows have no rank)."""
    if not line.strip() or SEPARATOR.match(line.strip()):
        return False
    return bool(re.match(r"\s*\d", line) or re.search(r"\b[12]\d{3}\b", line))


def find_tables(text):
    """Yield (header_idx or None, cols or None, [row lines]) for each standings
    block in one pre text. Driven by runs of data rows (a blank line ends a
    run); the header, if any, is found by looking just ABOVE the run — so both
    headed tables and bare round-robin grids are caught the same way."""
    lines = text.splitlines()
    n = len(lines)
    i = 0
    while i < n:
        if not data_row_looks_real(lines[i]):
            i += 1
            continue
        j = i
        while j < n and data_row_looks_real(lines[j]):
            j += 1
        rows = lines[i:j]
        if len(rows) >= 3 or _headed(lines, i):
            hidx, cols = _header_above(lines, i)
            yield hidx, cols, rows
        i = max(j, i + 1)


def _header_above(lines, run_start):
    """Look up to a few lines above a data run for a header line; return
    (index, cols) or (None, None)."""
    k = run_start - 1
    gap = 0
    while k >= 0 and gap < 3:
        s = lines[k].strip()
        if not s or SEPARATOR.match(s):
            k -= 1
            gap += 1
            continue
        ok, cols = is_header(lines[k])
        return (k, cols) if ok else (None, None)
    return None, None


def _headed(lines, run_start):
    return _header_above(lines, run_start)[0] is not None


def parse_pre(text, header_lines):
    """All crosstables in one pre block. header_lines is the shared line list
    for event-title lookup."""
    tables = []
    for header_idx, cols, rows in find_tables(text):
        headed = header_idx is not None
        players = []
        prev_rank = None
        for row in rows:
            res = (parse_data_row_header(row, cols, prev_rank) if headed
                   else parse_data_row_content(row, prev_rank))
            if res:
                player, prev_rank = res
                players.append(player)
        players = [p for p in players if p["name"]]
        # A named header is strong evidence. A headerless run must be a bit
        # bigger and look like real standings: carry ratings AND scores. That
        # last test rejects the player ranking/rating lists that share the
        # rank-name-fed-rating shape but have no result column.
        min_players = 2 if headed else 3
        if len(players) < min_players:
            continue
        if not headed and (
            sum(1 for p in players if p["rating"] is not None) < 2
            or sum(1 for p in players if p["score"] is not None) < 2
        ):
            continue
        title_line = header_idx if headed else _run_start(header_lines, rows)
        is_rr = any("*" in r for r in rows)
        tables.append({
            "event": event_title(header_lines, title_line) or "?",
            "header": " ".join(header_lines[header_idx].split()) if headed else "",
            "format": "round-robin" if is_rr else "swiss",
            "players": players,
        })
    return tables


def _run_start(lines, rows):
    """Index of the first row of `rows` within `lines` (for event-title lookup
    when a run has no header)."""
    if not rows:
        return 0
    try:
        return lines.index(rows[0])
    except ValueError:
        return 0


def read_file(path):
    text = open(path, encoding="utf-8", errors="replace").read()
    issue = issue_number(text)
    out = []
    for block in pre_blocks(text):
        # Each pre block is parsed on its own line grid so header-title lookup
        # stays within the block.
        lines = block.splitlines()
        for table in parse_pre(block, lines):
            table["source"] = "twic"
            table["ref"] = f"twic-{issue}" if issue else os.path.basename(path)
            out.append(table)
    return out


def gather(args):
    files = []
    for a in args:
        if os.path.isdir(a):
            files.extend(sorted(
                (os.path.join(a, n) for n in os.listdir(a) if n.lower().endswith((".html", ".htm"))),
                key=lambda p: int(re.search(r"\d+", os.path.basename(p)).group() or 0),
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
        k = args.index("--out")
        out_path = args[k + 1]
        del args[k:k + 2]
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
    rated = sum(1 for t in all_tables for p in t["players"] if p["rating"] is not None)
    print(
        f"{len(files)} files ({issues_with} with crosstables) -> "
        f"{len(all_tables)} crosstables, {players} players "
        f"(names {named}, ratings {rated}, scores {scored})",
        file=sys.stderr,
    )

    if summary:
        return
    out = json.dumps(all_tables, indent=2, ensure_ascii=False)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {out_path}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
