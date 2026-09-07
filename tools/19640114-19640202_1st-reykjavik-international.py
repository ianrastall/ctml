from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess.pgn

from ctml_build import child, fingerprints, game_termination, load_pgn, q, sha256, slug

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\dev\pgn\1964-reykjavik-intl.pgn")
METADATA = Path(r"D:\dev\pgn\1964-reykjavik-intl.meta.txt")
CHESSMETRICS = Path(r"D:\elysium\sources\chessmetrics\chessmetrics_ratings.csv")
OUTPUT = ROOT / "tours" / "19640114-19640202_1st-reykjavik-international.ctml"

SITE_SOURCE = SOURCE_PGN.as_uri()
CHESSGAMES_URL = "https://www.chessgames.com/perl/chess.pl?tid=80145"
CHESSMETRICS_EVENT_URL = (
    "http://chessmetrics.com/cm/CM2/SingleEvent.asp?"
    "Params=199510SSSSS3S140097196401131100361700033010100"
)
RESOLVER = "ctml-reykjavik1964-builder/1"
EVENT_REF = "event:19640114-19640202-1st-reykjavik-international"

# The one game whose moves are lost; the source PGN carries a result-only stub.
LOST_GAME = ("1", "Solmundarson, Magnus", "Wade, Robert Graham")

# Chessmetrics month for the pre-event rating column (the "January 1964 list").
# Eight of the fourteen carry a genuine period rating on that list; the other
# six were unrated then (they have only event-performance ratings, which are
# derived from these very games and are deliberately NOT stored as a rating).
# The authoritative source is the Chessmetrics event page; the local bulk CSV
# export is an incomplete corroboration that carries only four of the eight.
CM_MONTH = "1964.01"


@dataclass
class Player:
    name: str            # exact PGN display name ("Family, Given")
    federation: str
    placement: int
    titles: tuple[str, ...] = ()
    fide_id: str | None = None
    expect_score: float = 0.0
    expect_wdl: tuple[int, int, int] = (0, 0, 0)   # wins, draws, losses
    cm_id: str | None = None
    cm_rating: int | None = None
    note: str | None = None

    @property
    def slug(self) -> str:
        return slug(self.name)

    @property
    def pid(self) -> str:
        return f"p-{self.slug}"


# Roster in final-standing order.
#   * Expected score and W/D/L come from the published crosstable and standings
#     listing (see the .meta.txt source); the builder recomputes both from the
#     91 games and refuses to emit on any mismatch.
#   * cm_rating is the Chessmetrics "January 1964 list" pre-event rating, taken
#     from the Chessmetrics event page (authoritative). Eight players carry one;
#     the other six were unrated at event time (None). The local CSV corroborates
#     four of the eight and is silent (incomplete export) on the rest.
BJORNSSON_NOTE = (
    "Chessmetrics lists this player as 'Tomas Bjornsson'; contemporary game "
    "records and Reykjavík historical material identify him as Trausti Björnsson. "
    "The Chessmetrics name is not propagated and no Chessmetrics identity is linked."
)
ROSTER = [
    Player("Tal, Mihail",              "URS", 1,  ("GM",),  None,      12.5, (12, 1, 0), "129382", 2740),
    Player("Gligoric, Svetozar",       "YUG", 2,  ("GM",),  None,      11.5, (11, 1, 1), "044611", 2707),
    Player("Olafsson, Fridrik",        "ISL", 3,  ("GM",),  "2300052", 9.0,  (8, 2, 3),  "094938", 2670),
    Player("Johannessen, Svein",       "NOR", 3,  ("IM",),  None,      9.0,  (7, 4, 2),  "060046", 2507),
    Player("Wade, Robert Graham",      "ENG", 5,  ("IM",),  None,      7.5,  (5, 5, 3),  "140097", 2414),
    Player("Palmason, Gudmundur",      "ISL", 6,  (),       None,      7.0,  (3, 8, 2),  "097192", None),
    Player("Johannsson, Ingi Randver", "ISL", 7,  ("IM",),  None,      6.0,  (4, 4, 5),  None,     2533),
    Player("Gaprindashvili, Nona",     "URS", 8,  ("IM",),  "13600125",5.0,  (3, 4, 6),  "041552", None),
    Player("Solmundarson, Magnus",     "ISL", 8,  (),       "2300850", 5.0,  (2, 6, 5),  "123916", None),
    Player("Thorbergsson, Freysteinn", "ISL", 10, (),       None,      4.0,  (3, 2, 8),  "131212", 2385),
    Player("Gudmundsson, Arinbjorn",   "ISL", 10, (),       None,      4.0,  (2, 4, 7),  "048260", 2468),
    Player("Bjornsson, Trausti",       "ISL", 10, (),       None,      4.0,  (2, 4, 7),  None,     None, BJORNSSON_NOTE),
    Player("Kristinsson, Jon",         "ISL", 13, (),       "2300141", 3.5,  (2, 3, 8),  "069613", None),
    Player("Asmundsson, Ingvar",       "ISL", 14, (),       None,      3.0,  (1, 4, 8),  "005536", None),
]


def corroborate_chessmetrics(players: list[Player]) -> int:
    """Cross-check the hard-coded event-page ratings against the local CSV export.

    The Chessmetrics event page is authoritative for the ratings (hard-coded in
    ROSTER). This reads the bulk CSV's 1964.01 value for each player who has a
    Chessmetrics id and asserts consistency:
      * CSV value == hard-coded value            -> corroborated
      * CSV blank/absent, hard-coded value set   -> allowed (CSV export is
                                                    incomplete; page authoritative)
      * CSV blank/absent, hard-coded also None   -> agree (unrated at event)
      * anything else (a real disagreement)       -> hard fail
    Returns the number of ratings the CSV positively corroborated.
    """
    by_id = {p.cm_id: p for p in players if p.cm_id}
    csv_jan: dict[str, int | None] = {}
    with CHESSMETRICS.open(encoding="utf-8") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if header[:4] != ["PlayerID", "PlayerName", "Month", "Rating"]:
            raise ValueError(f"Unexpected Chessmetrics header: {header!r}")
        for pid, _name, month, rating, *_rest in reader:
            if pid in by_id and month == CM_MONTH:
                csv_jan[pid] = int(rating) if rating.strip() else None
    corroborated = 0
    for pid, p in by_id.items():
        got = csv_jan.get(pid)  # None if row absent or blank
        if got == p.cm_rating:
            if got is not None:
                corroborated += 1
        elif got is None and p.cm_rating is not None:
            continue  # CSV silent; event page authoritative
        else:
            raise ValueError(
                f"Chessmetrics disagreement for {p.name}: CSV {CM_MONTH}={got} "
                f"vs event-page {p.cm_rating}"
            )
    return corroborated


def game_pair(game: chess.pgn.Game) -> tuple[str, str, str]:
    h = game.headers
    return h["Round"], h["White"], h["Black"]


def is_lost(game: chess.pgn.Game) -> bool:
    return game_pair(game) == LOST_GAME


def audit(games: list[chess.pgn.Game], by_name: dict[str, Player]) -> None:
    if len(games) != 91:
        raise ValueError(f"Expected 91 records (90 games + 1 result-only stub), got {len(games)}")

    names = {p.name for p in ROSTER}
    seen = {g.headers["White"] for g in games} | {g.headers["Black"] for g in games}
    if seen != names:
        raise ValueError(f"PGN player set != roster. Extra={seen - names}, missing={names - seen}")

    # Complete single round robin: every unordered pair exactly once; 13 rounds x 7.
    pairs = Counter(frozenset((g.headers["White"], g.headers["Black"])) for g in games)
    if len(pairs) != 91 or any(c != 1 for c in pairs.values()):
        raise ValueError("Not a complete single round robin (a pairing is missing or duplicated)")
    if any(len(k) != 2 for k in pairs):
        raise ValueError("A game has identical white and black")
    rounds = Counter(g.headers["Round"] for g in games)
    if set(rounds) != {str(n) for n in range(1, 14)} or any(v != 7 for v in rounds.values()):
        raise ValueError(f"Expected 13 rounds of 7 games each, got {dict(rounds)}")

    # Exactly one lost game (the known result-only stub), drawn, with no real moves.
    lost = [g for g in games if is_lost(g)]
    if len(lost) != 1:
        raise ValueError(f"Expected exactly one lost/stub game, got {len(lost)}")
    if lost[0].headers["Result"] != "1/2-1/2":
        raise ValueError("The lost game's recorded result is not a draw")
    if len(list(lost[0].mainline_moves())) > 1:
        raise ValueError("The presumed result-only stub has real movetext")
    for g in games:
        if not is_lost(g) and len(list(g.mainline_moves())) < 2:
            raise ValueError(f"Unexpected empty/short movetext in {game_pair(g)}")

    # Recompute score and W/D/L for every player and match the published record.
    score: defaultdict[str, float] = defaultdict(float)
    wdl: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for g in games:
        w, b, r = g.headers["White"], g.headers["Black"], g.headers["Result"]
        if r == "1-0":
            score[w] += 1; wdl[w][0] += 1; wdl[b][2] += 1
        elif r == "0-1":
            score[b] += 1; wdl[b][0] += 1; wdl[w][2] += 1
        elif r == "1/2-1/2":
            score[w] += 0.5; score[b] += 0.5; wdl[w][1] += 1; wdl[b][1] += 1
        else:
            raise ValueError(f"Unsupported result {r!r} in {game_pair(g)}")

    for p in ROSTER:
        if score[p.name] != p.expect_score:
            raise ValueError(f"{p.name}: computed {score[p.name]} != published {p.expect_score}")
        if tuple(wdl[p.name]) != p.expect_wdl:
            raise ValueError(f"{p.name}: W/D/L {tuple(wdl[p.name])} != published {p.expect_wdl}")

    if sum(score.values()) != 91.0:
        raise ValueError(f"Total points {sum(score.values())} != 91.0")


def game_id(game: chess.pgn.Game, by_name: dict[str, Player]) -> str:
    h = game.headers
    return f"g-r{int(h['Round']):02d}-{by_name[h['White']].slug}-{by_name[h['Black']].slug}"


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    by_name = {p.name: p for p in ROSTER}
    if len(by_name) != 14:
        raise ValueError("Roster is not 14 distinct players")
    audit(games, by_name)
    corroborated = corroborate_chessmetrics(ROSTER)

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-reykjavik-1964"})
    header = child(root, "header")
    child(header, "name", "Reykjavik International 1964")
    er = child(header, "eventRef", ref=EVENT_REF, source=CHESSGAMES_URL)
    child(er, "name", "Reykjavik International (1st edition)")
    child(header, "eventType", "round-robin")
    child(header, "cadence", "classical")
    child(header, "federation", "ISL")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=1964, m=1, d=14, iso="1964-01-14")
    child(child(dates, "end"), "day", y=1964, m=2, d=2, iso="1964-02-02")
    place = child(header, "placeRef", ref="place:city:ISL-reykjavik", kind="city")
    child(place, "name", "Reykjavik")
    child(place, "country", "ISL")
    child(place, "city", "Reykjavik")
    child(header, "venue", "Lidó restaurant")
    orgs = child(header, "organizers")
    for o in ("Icelandic Chess Federation", "Reykjavík Chess Club"):
        oe = child(orgs, "organizer")
        child(oe, "name", o)
        child(oe, "role", "organizer")
    oe = child(orgs, "organizer")
    child(oe, "name", "Gunnarsson, Eiður")
    child(oe, "role", "tournament director")
    arbs = child(header, "arbiters")
    ae = child(arbs, "arbiter")
    child(ae, "name", "Pétursson, Áki")
    child(ae, "role", "referee")

    participants = child(root, "participants")
    for p in sorted(ROSTER, key=lambda x: (x.placement, x.name)):
        part = child(participants, "participant", id=p.pid)
        ref = child(part, "playerRef", ref=f"player:name:{p.slug}", source=CHESSGAMES_URL)
        family, given = (x.strip() for x in p.name.split(",", 1))
        name = child(ref, "name", display=p.name)
        child(name, "family", family)
        if given:
            child(name, "given", given)
        child(ref, "federation", p.federation)
        for t in p.titles:
            child(ref, "title", t)
        ids = child(ref, "ids")
        if p.fide_id:
            child(ids, "fideId", p.fide_id)
        child(ids, "internalId", f"reykjavik1964:{p.slug}")
        if p.cm_id:
            child(ids, "internalId", f"chessmetrics:{p.cm_id}")
        child(ref, "resolution", method="manual", resolver=RESOLVER,
              note="1964 event predating the FIDE rating/ID system; identified by name. "
                   "Any fideId is a modern retroactive identifier carried by the source PGN.")
        if p.cm_rating is not None:
            snap = child(part, "ratingSnapshot", system="chessmetrics", scope="standard")
            child(snap, "value", p.cm_rating)
            child(child(snap, "asOf"), "month", y=1964, m=1,
                  raw="Chessmetrics January 1964 list")
            child(snap, "publishedForEvent", "false")
        sc = p.expect_score
        child(part, "score", str(int(sc)) if float(sc).is_integer() else str(sc))
        child(part, "placement", p.placement)
        if p.note:
            child(part, "notes", p.note)

    games_el = child(root, "games")
    eco_n = term_n = played_n = 0
    for g in sorted(games, key=lambda x: (int(x.headers["Round"]),
                                          by_name[x.headers["White"]].slug,
                                          by_name[x.headers["Black"]].slug)):
        h = g.headers
        lost = is_lost(g)
        ge = child(games_el, "game", id=game_id(g, by_name), round=h["Round"],
                   white=by_name[h["White"]].pid, black=by_name[h["Black"]].pid,
                   result=h["Result"])
        if not lost and h.get("ECO"):
            child(ge, "eco", h["ECO"])
            eco_n += 1
        child(ge, "start", standard="true")
        if lost:
            src = child(ge, "source", kind="chessbase-pgn")
            child(src, "note", "Moves not available: this game is recorded result-only. "
                               "The source PGN carries only a placeholder ply. Result (draw) "
                               "is confirmed by the published crosstable.")
            continue
        nodes = list(g.mainline())
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
        src = child(ge, "source", kind="chessbase-pgn")
        child(src, "note", f"Date={h.get('Date', '')}; Site={h.get('Site', '')}. Untimed (1964); no clock data.")
        played_n += 1

    rated = [p.name for p in ROSTER if p.cm_rating is not None]
    notes = (
        "Reykjavik International, 1st edition, Reykjavik, Iceland, 14 January - 2 February 1964: "
        "a 14-player single round robin (13 rounds, 91 pairings), organized by the Icelandic Chess "
        "Federation and the Reykjavík Chess Club in memory of Pétur Zóphóníasson (1879-1946). Mikhail "
        "Tal won with 12.5/13 ahead of Svetozar Gligoric (11.5); Fridrik Olafsson and Svein Johannessen "
        "tied for third on 9. Five players came from abroad (Tal, Gligoric, Wade, Gaprindashvili, "
        "Johannessen); the rest were Icelandic. The builder recomputes every player's score AND "
        "win/draw/loss record from the 91 games and reproduces the published crosstable and standings; "
        "it also verifies the field is a complete single round robin (each pair once; 13 rounds of 7). "
        "One game's moves are lost - Solmundarson vs Wade, round 1, a draw - and is recorded result-only "
        "(no movetext, no fingerprints), so the round robin stays complete. The remaining 90 games carry "
        "full movetext (moves only; these were untimed 1964 games, so no clocks and no timeControl). ECO "
        "codes are taken from the source PGN's own headers, not reclassified. Player identity is by name "
        "(the event predates the FIDE rating/ID system); the source PGN supplies modern retroactive FIDE "
        "IDs for four players, preserved as ids. Titles and nationalities are as reported by the "
        "chessgames.com event description and its standings table; ties are recorded as shared placements "
        "(3=3, 8=8, 10=10=10). Ratings are Chessmetrics (Jeff Sonas) pre-event values from its January "
        f"1964 list (system=chessmetrics, publishedForEvent=false), present for {len(rated)} of the 14 "
        f"({', '.join(rated)}); the other six were unrated on that list and get no snapshot. No official "
        "FIDE rating can exist for 1964 (FIDE adopted the Elo system in 1970 and issued its first list in "
        "1971); Elo's own April-1964 Second International Rating List included none of these players (it "
        "required at least 25 games and a 2400 rating); and the Edo historical series stops at 1948, so it "
        "offers no fallback here. Chessmetrics also computes event-PERFORMANCE ratings for the six unrated "
        "players, but those are derived from these very games and are deliberately excluded (not stored as "
        "a rating). One data caveat: Chessmetrics misidentifies Trausti Björnsson as 'Tomas Bjornsson'; "
        "that name is not propagated and no Chessmetrics identity is linked for him. The event-page ratings "
        f"are authoritative; a local Chessmetrics CSV export corroborated {corroborated} of them and is an "
        "incomplete (blank) export for the rest. Uses the CTML 2.1 model (participant/placement)."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="chessbase-pgn")
    child(s1, "uri", SITE_SOURCE)
    child(s1, "note", f"91 records (90 games with moves + 1 result-only stub); moves only, no clocks. "
                      f"MegaBase/ChessBase (SourceTitle MCD). SHA-256 {sha256(SOURCE_PGN)}.")
    s2 = child(root, "source", kind="metadata")
    child(s2, "uri", METADATA.as_uri())
    child(s2, "note", f"Transcribed crosstable, official placings/ages/titles, W/D/L records, and event "
                      f"facts (dates, venue, organizers, referee). SHA-256 {sha256(METADATA)}.")
    s3 = child(root, "source", kind="chessgames-description")
    child(s3, "uri", CHESSGAMES_URL)
    child(s3, "note", "chessgames.com event description 'Reykjavik (1964)': the source for the narrative, "
                      "placings, ages, and reported titles. Cited, not reproduced (copyrighted prose).")
    s4 = child(root, "source", kind="chessmetrics-event")
    child(s4, "uri", CHESSMETRICS_EVENT_URL)
    child(s4, "note", "Chessmetrics (Jeff Sonas) event page for Reykjavik 1964; its rating column uses the "
                      "January 1964 list and is the authoritative source for the eight pre-event ratings.")
    s5 = child(root, "source", kind="chessmetrics-csv")
    child(s5, "uri", CHESSMETRICS.as_uri())
    child(s5, "note", f"Local Chessmetrics monthly-ratings CSV export; used to corroborate the {CM_MONTH} "
                      f"values ({corroborated} of 8 present, the rest blank in this export). "
                      f"SHA-256 {sha256(CHESSMETRICS)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(ROSTER), "records": len(games),
            "played_games": played_n, "lost_games": 1, "eco_games": eco_n,
            "terminations": term_n, "chessmetrics_rated": len(rated),
            "chessmetrics_csv_corroborated": corroborated,
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
