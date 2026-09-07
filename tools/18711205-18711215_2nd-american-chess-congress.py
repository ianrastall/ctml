"""Builder for the 2nd American Chess Congress, Cleveland, 5-15 December 1871.

Historical event: a 9-player round robin where every pair played until one side
had two decisive games (draws replayed and did NOT count toward the 'first to
2 wins' target, though they do score 1/2 point in the standings). Prizes were
awarded 1-7 in the first class; a small second class also ran alongside but is
not covered here (no game record survives).

Sources
-------
- **Primary game source**: a ChessBase "EXT 2002" collection PGN (68 game
  entries: 67 real games with full movetext + one 0-move stub for the
  Hos-Har forfeit of 13 December). This is a dramatic upgrade over the
  27-game chessgames.com collection this file used to build against.
- **Crosstable / narrative**: chessgames.com introduction (rewritten by
  Tabanus in 2016 from Ohio newspapers). The crosstable is authoritative for
  aggregate results and for the six real games whose movetext was never
  preserved (per the chessgames.com narrative: Johnston 1 Haughton and
  Haughton 0 Johnston on Dec 5, Haughton 0 Judd and Judd 1 Haughton on Dec 6,
  Johnston 1 Harding on Dec 11, and Harding 1 Johnston on Dec 12).
- **Tournament book**: "Book of the Second American Chess Congress"
  (Brownson, Dubuque, 1872), transcribed at
  D:\\elysium\\sources\\twic\\second-chess-congress.txt. Source of the
  tournament rules, the participant list, and the classical algebraic notation
  the ChessBase PGN was itself derived from.

The builder emits every real pairing plus every forfeit:
  - 67 real games with full movetext (from the ChessBase PGN)
  - 6 result-only real games (Har-Jo x2, Ju-Hg x2, Jo-Hg x2), colors unknown
    and assigned by convention (alphabetically-first slug plays White)
  - 7 forfeit records (Hos-Har once, Ware-Hg / Sm-Hg / Har-Hg twice each),
    termination="forfeit"; the Hos-Har forfeit is the ChessBase 0-move stub,
    the other six come from the crosstable

For each participant it recomputes wins/losses/draws (real games only, forfeits
excluded, matching the crosstable summary convention) and audits against the
crosstable-derived expected standings; the builder refuses to emit on mismatch.

Standings note
--------------
Chessgames.com publishes both a top-line standings summary and a granular
crosstable that DISAGREE for Judd, Harding, and Johnston. The crosstable's
per-cell characters (whose two opponent-perspective views cross-check each
other, agree with the "73 total real games" figure, agree with the specific
missing-game narrative, and now also agree with this ChessBase PGN's per-pair
totals) are treated as authoritative here; the summary is stale. See tournament
`<notes>` for the call-out.

No player federations or titles are asserted (neither existed in this form in
1871 America). No ratings are recorded (the ChessBase PGN carries none; Edo
has estimates for some players but they are not brought into this file).
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, q, sha256, slug

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\2nd-ch-co.pgn")
BOOK_TEXT = Path(r"D:\elysium\sources\twic\second-chess-congress.txt")
OUTPUT = ROOT / "tours" / "18711205-18711215_2nd-american-chess-congress.ctml"
SITE_SOURCE = SOURCE_PGN.as_uri()
RESOLVER = "ctml-acc1871-builder/2"

# Roster in final placement order (1..9): slug, canonical display, family, given, city
ROSTER = [
    ("mackenzie", "Mackenzie, George Henry", "Mackenzie", "George Henry", "New York"),
    ("hosmer",    "Hosmer, Henry",           "Hosmer",    "Henry",         "Chicago"),
    ("elder",     "Elder, Frederick H.",     "Elder",     "Frederick H.",  "Detroit"),
    ("judd",      "Judd, Max",               "Judd",      "Max",           "Cleveland"),
    ("ware",      "Ware, Preston, Jr.",      "Ware",      "Preston, Jr.",  "Boston"),
    ("smith",     "Smith, Harsen Darwin",    "Smith",     "Harsen Darwin", "Cassopolis, MI"),
    ("harding",   "Harding, Henry",          "Harding",   "Henry",         "East Saginaw, MI"),
    ("johnston",  "Johnston, A.",            "Johnston",  "A.",            "Cincinnati"),
    ("haughton",  "Haughton, William B.",    "Haughton",  "William B.",    "Chicago"),
]
# The ChessBase PGN spells names slightly differently (e.g. "MacKenzie" with a
# capital K, "Johnston, A1." with an OCR-ish trailing "1.", "Elder, Frederic"
# without the middle initial). Map every spelling seen in the source to our
# canonical slug rather than editing the PGN.
PGN_NAME_TO_SLUG = {
    "MacKenzie, George Henry": "mackenzie",
    "Hosmer, Henry":            "hosmer",
    "Elder, Frederic":          "elder",
    "Judd, Max":                "judd",
    "Ware, Preston":            "ware",
    "Smith, Harsen Darwin":     "smith",
    "Harding, Henry":           "harding",
    "Johnston, A1.":            "johnston",
    "Haughton, William B":      "haughton",
}
PLACEMENT = {s: i for i, (s, *_) in enumerate(ROSTER, start=1)}
PRIZES = {1: 100, 2: 50, 3: 40, 4: 35, 5: 30, 6: 25, 7: 15}

# Authoritative per-pair aggregate from the chessgames.com crosstable.
# Every real game in the ChessBase PGN is cross-checked against these totals,
# and any real pairing NOT present in the PGN is filled in as result-only
# games (colors unknown, arbitrarily assigned).
# Format: (a, b, a_wins, b_wins, draws, ff_by, ff_count)
PAIRINGS = [
    # Row 1: Mackenzie
    dict(a="mackenzie", b="hosmer",    a_wins=1, b_wins=1, draws=1, ff_by=None, ff_count=0),
    dict(a="elder",     b="mackenzie", a_wins=1, b_wins=1, draws=1, ff_by=None, ff_count=0),
    dict(a="judd",      b="mackenzie", a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="mackenzie", b="ware",      a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="mackenzie", b="smith",     a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="harding",   b="mackenzie", a_wins=0, b_wins=2, draws=1, ff_by=None, ff_count=0),
    dict(a="johnston",  b="mackenzie", a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="haughton",  b="mackenzie", a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    # Row 2: Hosmer
    dict(a="elder",     b="hosmer",    a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="hosmer",    b="judd",      a_wins=2, b_wins=0, draws=1, ff_by=None, ff_count=0),
    dict(a="hosmer",    b="ware",      a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="hosmer",    b="smith",     a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    # Hos-Har: 1 real win by Hosmer + 1 forfeit win to Hosmer (Harding ill).
    # The ChessBase PGN has the forfeit as a 0-move Hos-Har 1-0 stub.
    dict(a="harding",   b="hosmer",    a_wins=0, b_wins=1, draws=0, ff_by="hosmer", ff_count=1),
    dict(a="hosmer",    b="johnston",  a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="haughton",  b="hosmer",    a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    # Row 3: Elder
    dict(a="elder",     b="judd",      a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    dict(a="elder",     b="ware",      a_wins=1, b_wins=1, draws=2, ff_by=None, ff_count=0),
    dict(a="elder",     b="smith",     a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="elder",     b="harding",   a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="elder",     b="johnston",  a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="elder",     b="haughton",  a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    # Row 4: Judd
    dict(a="judd",      b="ware",      a_wins=2, b_wins=0, draws=0, ff_by=None, ff_count=0),
    dict(a="judd",      b="smith",     a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    dict(a="harding",   b="judd",      a_wins=0, b_wins=2, draws=1, ff_by=None, ff_count=0),
    dict(a="johnston",  b="judd",      a_wins=0, b_wins=2, draws=1, ff_by=None, ff_count=0),
    dict(a="haughton",  b="judd",      a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    # Row 5: Ware
    dict(a="smith",     b="ware",      a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    dict(a="harding",   b="ware",      a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    dict(a="johnston",  b="ware",      a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="haughton",  b="ware",      a_wins=0, b_wins=0, draws=0, ff_by="ware", ff_count=2),
    # Row 6: Smith
    dict(a="harding",   b="smith",     a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="johnston",  b="smith",     a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
    dict(a="haughton",  b="smith",     a_wins=0, b_wins=0, draws=0, ff_by="smith", ff_count=2),
    # Row 7: Harding
    dict(a="harding",   b="johnston",  a_wins=1, b_wins=1, draws=0, ff_by=None, ff_count=0),
    dict(a="harding",   b="haughton",  a_wins=0, b_wins=0, draws=0, ff_by="harding", ff_count=2),
    # Row 8: Johnston
    dict(a="haughton",  b="johnston",  a_wins=0, b_wins=2, draws=0, ff_by=None, ff_count=0),
]


def load_pgn(path: Path) -> list[chess.pgn.Game]:
    raw = path.read_text(encoding="utf-8-sig")
    stream = io.StringIO(raw)
    games: list[chess.pgn.Game] = []
    while True:
        g = chess.pgn.read_game(stream)
        if g is None:
            break
        if g.errors:
            raise ValueError(f"PGN errors in game {len(games) + 1}: {g.errors}")
        games.append(g)
    return games


def game_pair(g: chess.pgn.Game) -> tuple[str, str]:
    """Return (white_slug, black_slug), stripping the exact PGN name spellings."""
    w = PGN_NAME_TO_SLUG[g.headers["White"].strip()]
    b = PGN_NAME_TO_SLUG[g.headers["Black"].strip()]
    return w, b


def derive_standings() -> dict[str, dict[str, int | float]]:
    """Compute per-player expected standings from PAIRINGS (crosstable truth)."""
    out: dict[str, dict[str, int | float]] = {
        s: {"wins": 0, "losses": 0, "draws": 0, "games": 0,
            "forfeit_wins": 0, "forfeit_losses": 0}
        for s, *_ in ROSTER
    }
    for p in PAIRINGS:
        a, b = p["a"], p["b"]
        out[a]["wins"]   += p["a_wins"]
        out[b]["wins"]   += p["b_wins"]
        out[a]["losses"] += p["b_wins"]
        out[b]["losses"] += p["a_wins"]
        out[a]["draws"]  += p["draws"]
        out[b]["draws"]  += p["draws"]
        out[a]["games"]  += p["a_wins"] + p["b_wins"] + p["draws"]
        out[b]["games"]  += p["a_wins"] + p["b_wins"] + p["draws"]
        if p["ff_count"] > 0:
            ff = p["ff_by"]
            other = a if ff != a else b
            out[ff]["forfeit_wins"]     += p["ff_count"]
            out[other]["forfeit_losses"] += p["ff_count"]
    for rec in out.values():
        rec["score"] = rec["wins"] + 0.5 * rec["draws"]
    return out


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    if len(games) != 68:
        raise ValueError(f"Expected 68 games in ChessBase PGN, got {len(games)}")

    # ---- Categorize PGN games per unordered pairing ----
    pairing_games: dict[frozenset, list[chess.pgn.Game]] = defaultdict(list)
    for g in games:
        w, b = game_pair(g)
        pairing_games[frozenset((w, b))].append(g)

    # ---- Emit ----
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-cr-acc-1871"})
    header = child(root, "header")
    child(header, "name", "2nd American Chess Congress")
    er = child(header, "eventRef", ref="event:18711205-18711215-2nd-american-chess-congress", source=SITE_SOURCE)
    child(er, "name", "2nd American Chess Congress")
    child(header, "eventType", "round-robin")
    child(header, "cadence", "classical")
    child(header, "federation", "USA")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1871, m=12, d=5,  iso="1871-12-05")
    child(child(dates, "end"),   "day", y=1871, m=12, d=15, iso="1871-12-15")
    place = child(header, "placeRef", ref="place:city:USA-cleveland", kind="city")
    child(place, "name", "Cleveland")
    child(place, "country", "USA")
    child(place, "city", "Cleveland")
    child(header, "venue", "Kennard House, Cleveland, Ohio")
    orgs = child(header, "organizers")
    for name in ("Detroit Chess Club", "Cleveland Chess Club"):
        oe = child(orgs, "organizer")
        child(oe, "name", name)
        child(oe, "role", "organizer")
    arbs = child(header, "arbiters")
    ar = child(arbs, "arbiter")
    child(ar, "name", "Yates, W. G.")
    child(ar, "role", "referee")

    # ---- Participants ----
    derived = derive_standings()
    participants = child(root, "participants")
    for i, (s, display, family, given, city) in enumerate(ROSTER, start=1):
        rec = derived[s]
        placement = PLACEMENT[s]
        part = child(participants, "participant", id=f"p-{s}")
        ref = child(part, "playerRef", ref=f"player:name:{slug(display)}")
        name = child(ref, "name", display=display)
        child(name, "family", family)
        if given:
            child(name, "given", given)
        ids = child(ref, "ids")
        child(ids, "internalId", f"acc1871:{s}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="2nd American Chess Congress participant; identified by name (no FIDE id).")
        child(part, "seed", i)  # participants listed in the tournament book's opening/final order
        score_val = int(rec["score"]) if float(rec["score"]).is_integer() else float(rec["score"])
        child(part, "score", score_val)
        child(part, "placement", placement)
        parts = [f"Home city: {city}."]
        if placement in PRIZES:
            parts.append(f"Prize: ${PRIZES[placement]}.")
        if placement == 9:
            parts.append("Withdrew after 7-8 December following ten straight losses; "
                         "his three remaining opponents (Ware, Smith, Harding) were "
                         "awarded a forfeit pair each.")
        parts.append(
            f"Crosstable-derived record over real games: {rec['wins']}W-{rec['losses']}L-"
            f"{rec['draws']}D in {rec['games']} games (forfeits, tallied separately: "
            f"+{rec['forfeit_wins']} -{rec['forfeit_losses']})."
        )
        child(part, "notes", " ".join(parts))

    # ---- Games ----
    games_el = child(root, "games")
    overall = {s: {"wins": 0, "losses": 0, "draws": 0} for s, *_ in ROSTER}
    game_counter = 0
    eco_n = term_n = 0
    real_movetext = result_only = forfeit_count = 0

    def new_gid(kind: str, a: str, b: str) -> str:
        nonlocal game_counter
        game_counter += 1
        return f"g-{game_counter:03d}-{kind}-{'-'.join(sorted((a, b)))}"

    def tally(result: str, w: str, b: str):
        if result == "1-0":
            overall[w]["wins"]   += 1
            overall[b]["losses"] += 1
        elif result == "0-1":
            overall[b]["wins"]   += 1
            overall[w]["losses"] += 1
        elif result == "1/2-1/2":
            overall[w]["draws"] += 1
            overall[b]["draws"] += 1
        else:
            raise ValueError(f"Unexpected result {result!r}")

    def emit_pgn_game(g: chess.pgn.Game):
        nonlocal eco_n, term_n, real_movetext, forfeit_count
        h = g.headers
        w, b = game_pair(g)
        result = h["Result"]
        nodes = list(g.mainline())
        # The ChessBase PGN preserves the Hos-Har forfeit of Dec 13 as a 0-move
        # stub with Result "1-0"; emit it with termination="forfeit" (NOT with
        # movetext, since none exists) and count it as a forfeit rather than a
        # real game.
        is_forfeit_stub = (len(nodes) == 0 and w == "hosmer" and b == "harding" and result == "1-0")
        if is_forfeit_stub:
            forfeit_count += 1
            gid = new_gid("forfeit", w, b)
            ge = child(games_el, "game", id=gid, round="?", white=f"p-{w}", black=f"p-{b}", result=result)
            child(ge, "termination", "forfeit")
            src = child(ge, "source", kind="chessbase-ext-2002")
            child(src, "note",
                  "Hosmer-Harding forfeit of 13 December 1871 (Harding withdrew "
                  "for the day due to illness). The ChessBase PGN preserves this "
                  "as a 0-move Hos-Har 1-0 entry; recorded here with "
                  "termination=\"forfeit\". Not counted toward Hosmer's real "
                  "wins or Harding's real losses.")
            return

        real_movetext += 1
        gid = new_gid("real", w, b)
        ge = child(games_el, "game", id=gid, round="?", white=f"p-{w}", black=f"p-{b}", result=result)
        if h.get("ECO") and h["ECO"] not in ("?", ""):
            child(ge, "eco", h["ECO"])
            eco_n += 1
        child(ge, "start", standard="true")
        moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo="false")
        for n in nodes:
            child(moves, "move", ply=n.ply(), value=n.move.uci())
        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chessbase-ext-2002")
        cb_note = f"ChessBase EXT 2002 (SourceDate {h.get('SourceDate','?')})."
        if h.get("GameId"):
            cb_note += f" GameId={h['GameId']}."
        if h.get("EventDate"):
            cb_note += f" EventDate={h['EventDate']}."
        child(src, "note", cb_note)
        tally(result, w, b)

    def emit_result_only(a: str, b: str, result: str, aggregate_note: str):
        nonlocal result_only
        result_only += 1
        gid = new_gid("resonly", a, b)
        ge = child(games_el, "game", id=gid, round="?", white=f"p-{a}", black=f"p-{b}", result=result)
        src = child(ge, "source", kind="crosstable-aggregate")
        child(src, "note", aggregate_note)
        tally(result, a, b)

    def emit_forfeit(winner: str, loser: str, note: str):
        nonlocal forfeit_count
        forfeit_count += 1
        gid = new_gid("forfeit", winner, loser)
        # Convention: forfeit winner plays White; result 1-0.
        ge = child(games_el, "game", id=gid, round="?", white=f"p-{winner}", black=f"p-{loser}", result="1-0")
        child(ge, "termination", "forfeit")
        src = child(ge, "source", kind="crosstable-aggregate")
        child(src, "note", note)

    # Process each pairing in canonical order so all real games for a pair are
    # emitted first, followed by any inferred result-only games, then forfeits.
    for p in PAIRINGS:
        a, b = p["a"], p["b"]
        key = frozenset((a, b))
        pgn_games = pairing_games.pop(key, [])

        # Track what the PGN contributes to this pair.
        pgn_real_a_wins = 0
        pgn_real_b_wins = 0
        pgn_real_draws  = 0
        for g in pgn_games:
            emit_pgn_game(g)
            h = g.headers
            w, bl = game_pair(g)
            result = h["Result"]
            nodes = list(g.mainline())
            # Skip the forfeit stub from the tally (it's a forfeit, not a real game).
            if len(nodes) == 0:
                continue
            if result == "1/2-1/2":
                pgn_real_draws += 1
            elif result == "1-0":
                if w == a:
                    pgn_real_a_wins += 1
                else:
                    pgn_real_b_wins += 1
            elif result == "0-1":
                if bl == a:
                    pgn_real_a_wins += 1
                else:
                    pgn_real_b_wins += 1

        # Reconcile with crosstable expectations.
        need_a = p["a_wins"] - pgn_real_a_wins
        need_b = p["b_wins"] - pgn_real_b_wins
        need_d = p["draws"]  - pgn_real_draws
        if need_a < 0 or need_b < 0 or need_d < 0:
            raise ValueError(
                f"Pair {a}-{b}: PGN wins/draws ({pgn_real_a_wins}/{pgn_real_b_wins}/"
                f"{pgn_real_draws}) exceed crosstable ({p['a_wins']}/{p['b_wins']}/{p['draws']})"
            )
        if need_a + need_b + need_d > 0:
            aggregate_note = (
                f"Real tournament game between {a} and {b} whose movetext is not "
                f"preserved in the ChessBase EXT 2002 PGN; result taken from the "
                f"chessgames.com crosstable (pairing aggregate: "
                f"{p['a_wins']}W-{p['b_wins']}L-{p['draws']}D for {a} vs {b}). "
                f"Colors for this game are unknown and assigned by convention "
                f"(alphabetically-first slug plays White)."
            )
            for _ in range(need_a):
                emit_result_only(a, b, "1-0", aggregate_note)
            for _ in range(need_b):
                emit_result_only(a, b, "0-1", aggregate_note)
            for _ in range(need_d):
                emit_result_only(a, b, "1/2-1/2", aggregate_note)

        # Handle any crosstable forfeits that the PGN didn't already carry
        # (the Hos-Har stub is emitted via emit_pgn_game and already counted).
        expected_ff = p["ff_count"]
        already_ff_in_pgn = sum(1 for g in pgn_games if len(list(g.mainline_moves())) == 0)
        remaining_ff = expected_ff - already_ff_in_pgn
        if remaining_ff > 0:
            ff = p["ff_by"]
            other = a if ff != a else b
            for _ in range(remaining_ff):
                if other == "haughton":
                    reason = ("Haughton withdrew from the tournament on 7-8 December "
                              "1871 after ten straight losses; two forfeit wins were "
                              f"awarded to {ff}.")
                else:
                    reason = f"Forfeit awarded to {ff} against {other}."
                emit_forfeit(ff, other, "Source: chessgames.com crosstable. " + reason)

    if pairing_games:
        leftovers = [(sorted(k), len(v)) for k, v in pairing_games.items()]
        raise ValueError(f"PGN games belong to unknown pairings: {leftovers}")

    # ---- Audit standings against crosstable ----
    for s, exp in derived.items():
        got = overall[s]
        if got["wins"] != exp["wins"] or got["losses"] != exp["losses"] or got["draws"] != exp["draws"]:
            raise ValueError(
                f"Standings mismatch for {s}: emitted games gave W-L-D "
                f"{got['wins']}-{got['losses']}-{got['draws']}, crosstable expects "
                f"{exp['wins']}-{exp['losses']}-{exp['draws']}"
            )

    # ---- Tournament notes and sources ----
    total_games_emitted = game_counter
    notes = (
        "2nd American Chess Congress, Kennard House, Cleveland, Ohio, "
        "5-15 December 1871. A 9-player round robin organized by the Detroit "
        "and Cleveland Chess Clubs. Format: each pair played until one side "
        "had two decisive games (draws were replayed and did NOT count toward "
        "the 'first to 2 wins' target, though they do score 1/2 point in the "
        "standings); 73 real games were played in total, plus seven forfeit "
        "slots (William Haughton withdrew on 7-8 December 1871 after ten "
        "straight losses, giving Ware, Smith and Harding two forfeit wins "
        "each; one Hosmer-Harding game on 13 December was forfeited due to "
        "Harding's illness). No time control was recorded beyond '12 moves to "
        "the hour' and the daily sessions 9-12, 2-5, 7-10 stipulated in the "
        "rules. Prizes: 1 Mackenzie $100, 2 Hosmer $50, 3 Elder $40, 4 Judd "
        "$35, 5-6 Ware / Smith $25 each, 7 Harding $15. George Henry "
        "Mackenzie won with 15.5/19 (+14 -2 =3), dropping decisive games only "
        "to Hosmer and Elder (with whom he was even). "
        "PRIMARY SOURCE: a ChessBase 'EXT 2002' collection PGN of 68 entries "
        "(67 real games with full movetext, plus one 0-move stub for the "
        "Hos-Har forfeit of 13 December). Six real games were never preserved "
        "and are known only from the chessgames.com narrative: Johnston 1 "
        "Haughton and Haughton 0 Johnston on Dec 5, Haughton 0 Judd and Judd "
        "1 Haughton on Dec 6, Johnston 1 Harding on Dec 11, and Harding 1 "
        "Johnston on Dec 12. These six are emitted as result-only records "
        "against the pairing aggregates from the chessgames.com crosstable, "
        "with colors arbitrarily assigned (alphabetically-first slug plays "
        "White) since the source does not preserve them. The remaining six "
        "forfeits (three ++ cells vs Haughton x 2 forfeits each) are emitted "
        "as games with termination='forfeit'. "
        "SOURCE DISCREPANCY (documented, not fatal): chessgames.com publishes "
        "both a top-line standings summary and a granular crosstable that "
        "disagree for three players. The summary shows Judd 9.5/17 (+8 -6 =3), "
        "Harding 2/13 (+1 -10 =2), Johnston 0.5/13 (+0 -12 =1); the "
        "crosstable's per-cell W/D/L characters (whose two opponent-perspective "
        "views cross-check each other, agree with the '73 total real games' "
        "figure, agree with the specific missing-game narrative, and now also "
        "agree with this ChessBase PGN's per-pair totals) sum to Judd 11.5/19 "
        "(+10 -6 =3), Harding 3/15 (+2 -11 =2), Johnston 3.5/18 (+3 -14 =1). "
        "The crosstable is treated as authoritative here; the summary is stale. "
        "The builder recomputes each participant's wins/losses/draws from every "
        "emitted real game and refuses to emit if the totals do not match the "
        "crosstable. No player federations or titles are asserted (neither "
        "existed in this form in 1871 America). No ratings are recorded (the "
        "ChessBase PGN carries none). "
        f"Total emitted game records: {total_games_emitted} = {real_movetext} with "
        f"movetext + {result_only} result-only + {forfeit_count} forfeit."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chessbase-ext-2002")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note",
          f"68 entries (67 real games with movetext + 1 zero-move Hos-Har forfeit stub); "
          f"SHA-256 {sha256(SOURCE_PGN)}.")
    if BOOK_TEXT.exists():
        s2 = child(root, "source", kind="metadata")
        child(s2, "uri", BOOK_TEXT.as_uri())
        child(s2, "note",
              "Transcribed 'Book of the Second American Chess Congress' (Brownson, "
              "Dubuque, 1872) and chessgames.com introduction/crosstable (source of "
              "the per-pairing aggregates, missing-game narrative, and forfeit "
              f"accounting); SHA-256 {sha256(BOOK_TEXT)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(ROSTER),
            "games_movetext": real_movetext, "games_result_only": result_only,
            "games_forfeit": forfeit_count, "games_total": total_games_emitted,
            "eco_games": eco_n, "terminations_from_movetext": term_n,
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
