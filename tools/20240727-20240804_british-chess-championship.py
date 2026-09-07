"""British Chess Championship 2024 - Championship section (Hull, 27 July - 4 August 2024).

An 86-player, 9-round classical Swiss at Hull City Hall, followed by a four-game
rapid+blitz playoff for the title. Built from the chess.com broadcast PGN, with
the official Swiss-Manager starting rank supplying seed, federation, sex and the
event rating - none of which the PGN carries.

Three things in the source need handling rather than parsing:

  * Two BYES (round 1 Madhavan, round 7 Atako) appear as pseudo-games with
    Black="bye", zero moves and a chess.com ".../-bye" Site URL. They are
    ctml:nonGame kind="bye-full", not games: there is no opponent and no board
    result between two sides.
  * One DOUBLE FORFEIT (round 9, Saunders-Larkin) has the literal movetext
    "0-0", which is not SAN and makes python-chess raise. It is a real scheduled
    pairing that neither side played, so per ctml-core's NonGameType note it
    stays an ordinary ctml:game with result="0-0" termination="forfeit" and no
    moves. load_games() below tolerates exactly this one shape and nothing else.
  * The FIDE list rolls over MID-EVENT. Every player's PGN Elo is the July 2024
    rating through 1 August and the August 2024 rating from round 7 (2024.08.02)
    onward; 42 of the 86 changed. The builder asserts that each player's
    pre-rollover Elo equals the starting-rank rating, stores that as the
    ratingSnapshot ("event starting rating"), and records the post-rollover value
    in the notes rather than silently picking one of the two.

Standings: computed from the 379 played games plus the 2 byes (381 points, which
the builder asserts). No official final crosstable was available, so tiebreak
order is NOT invented: participant/score is asserted for all 86, but
participant/placement only for the two players the playoff actually decided -
Gawain Jones (champion) and David Howell.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn

from ctml_build import (child, classify_eco, fingerprints, game_termination,
                        load_eco, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2024-british-chess-championship.pgn")
STARTING_RANK = ROOT / "sources" / "20240727-20240804_british-chess-championship_starting-rank.tsv"
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20240727-20240804_british-chess-championship.ctml"

RESULTS_URL = "https://chess-results.com/tnr373174.aspx?lan=1"
RESOLVER = "ctml-british2024-builder/1"
EVENT_REF = "event:20240727-20240804-british-chess-championship-2024"
FIDE_EVENT_ID = "373174"

ROUNDS = 9
PLAYOFF_ROUNDS = (10, 11, 12, 13)
RATING_ROLLOVER = "2024.08.02"        # first date on the August 2024 FIDE list
CLASSICAL_TC = "5400+30"
TC_PROSE = ("40 moves in 90 minutes, followed by 30 minutes to complete the game, "
            "plus 30 seconds added per move from move 1")

# PlayerTitleType (ctml-vocab.xsd). "AIM" on the starting rank is an arena title,
# not one of these, and the PGN asserts no title for that player, so it is dropped.
VALID_TITLES = {"GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM"}

CHAMPION_FIDE = "409561"    # Gawain Jones, won the playoff 2.5-1.5
RUNNERUP_FIDE = "410608"    # David Howell


@dataclass(frozen=True)
class Starter:
    seed: int
    title: str        # official starting-rank title ("" if untitled)
    name: str         # official Swiss-Manager spelling
    fide_id: str
    federation: str
    rating: int
    sex: str          # "w" on the starting rank's sex column, else ""

    @property
    def pid(self) -> str:
        return f"p-fide-{self.fide_id}"

    @property
    def family(self) -> str:
        return self.name.split(",", 1)[0].strip()

    @property
    def given(self) -> str:
        parts = self.name.split(",", 1)
        return parts[1].strip() if len(parts) > 1 else ""


def load_starting_rank(path: Path) -> dict[str, Starter]:
    starters: dict[str, Starter] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        cells = line.split("\t")
        if len(cells) < 6:
            raise ValueError(f"Malformed starting-rank row: {line!r}")
        seed, title, name, fide_id, fed, rating = (c.strip() for c in cells[:6])
        sex = cells[6].strip() if len(cells) > 6 else ""
        if fide_id in starters:
            raise ValueError(f"Duplicate FIDE id {fide_id}")
        starters[fide_id] = Starter(int(seed), title, name, fide_id, fed, int(rating), sex)
    seeds = sorted(s.seed for s in starters.values())
    if seeds != list(range(1, len(starters) + 1)):
        raise ValueError("Starting-rank seeds are not 1..N without gaps")
    return starters


def load_games(path: Path) -> list[chess.pgn.Game]:
    """Read the PGN, tolerating the single double-forfeit game whose movetext is
    the literal string "0-0". Any other parse error is still fatal."""
    games: list[chess.pgn.Game] = []
    with path.open(encoding="utf-8-sig") as stream:
        while True:
            game = chess.pgn.read_game(stream)
            if game is None:
                break
            if game.errors:
                forfeit = (game.headers.get("Result") == "0-0"
                           and not list(game.mainline_moves())
                           and all("0-0" in str(e) for e in game.errors))
                if not forfeit:
                    raise ValueError(f"PGN errors in game {len(games) + 1}: {game.errors}")
            games.append(game)
    return games


def is_bye(game: chess.pgn.Game) -> bool:
    return game.headers.get("Black") == "bye"


def decimal(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def audit(starters: dict[str, Starter], games: list[chess.pgn.Game]) -> dict[str, object]:
    """Reconcile the roster, the mid-event rating rollover, and the point total."""
    byes = [g for g in games if is_bye(g)]
    playoff = [g for g in games if not is_bye(g) and int(g.headers["Round"]) in PLAYOFF_ROUNDS]
    swiss = [g for g in games if not is_bye(g) and int(g.headers["Round"]) <= ROUNDS]
    if len(byes) + len(playoff) + len(swiss) != len(games):
        raise ValueError("Game partition does not cover the source")
    if len(playoff) != len(PLAYOFF_ROUNDS):
        raise ValueError(f"Expected {len(PLAYOFF_ROUNDS)} playoff games, got {len(playoff)}")

    # roster identity
    seen = set()
    for g in swiss + playoff:
        seen.update({g.headers["WhiteFideId"], g.headers["BlackFideId"]})
    for g in byes:
        seen.add(g.headers["WhiteFideId"])
    if seen != set(starters):
        raise ValueError(f"Roster mismatch vs starting rank: {seen ^ set(starters)}")

    # ratings: pre-rollover Elo must equal the starting-rank rating
    pre: defaultdict[str, set[int]] = defaultdict(set)
    post: defaultdict[str, set[int]] = defaultdict(set)
    for g in swiss:
        h = g.headers
        bucket = pre if h["Date"] < RATING_ROLLOVER else post
        for who in ("White", "Black"):
            bucket[h[f"{who}FideId"]].add(int(h[f"{who}Elo"]))
    for fid, values in pre.items():
        if values != {starters[fid].rating}:
            raise ValueError(f"{fid}: pre-rollover Elo {values} != starting rank {starters[fid].rating}")
    august = {fid: sorted(v)[0] for fid, v in post.items() if len(v) == 1}
    changed = {fid: v for fid, v in august.items() if v != starters[fid].rating}

    # titles: the starting rank and the PGN must not contradict each other
    pgn_titles: defaultdict[str, set[str]] = defaultdict(set)
    for g in swiss + playoff:
        h = g.headers
        for who in ("White", "Black"):
            if h.get(f"{who}Title"):
                pgn_titles[h[f"{who}FideId"]].add(h[f"{who}Title"])
    for fid, titles in pgn_titles.items():
        if len(titles) > 1:
            raise ValueError(f"{fid}: PGN gives conflicting titles {titles}")
        official = starters[fid].title
        if official and official in VALID_TITLES and official not in titles:
            raise ValueError(f"{fid}: starting rank says {official}, PGN says {titles}")

    # scores from the 9-round Swiss plus byes
    score: defaultdict[str, float] = defaultdict(float)
    played: defaultdict[str, int] = defaultdict(int)
    forfeits = []
    for g in swiss:
        h = g.headers
        w, b, res = h["WhiteFideId"], h["BlackFideId"], h["Result"]
        played[w] += 1
        played[b] += 1
        if res == "1-0":
            score[w] += 1.0
        elif res == "0-1":
            score[b] += 1.0
        elif res == "1/2-1/2":
            score[w] += 0.5
            score[b] += 0.5
        elif res == "0-0":
            forfeits.append(g)
        else:
            raise ValueError(f"Unsupported result {res!r}")
    for g in byes:
        score[g.headers["WhiteFideId"]] += 1.0

    real_games = len(swiss) - len(forfeits)
    expected_points = real_games + len(byes)
    if abs(sum(score.values()) - expected_points) > 1e-9:
        raise ValueError(f"Points {sum(score.values())} != {expected_points}")

    # the playoff must be a two-player, four-game tie between the joint leaders
    contestants = {h for g in playoff for h in (g.headers["WhiteFideId"], g.headers["BlackFideId"])}
    if contestants != {CHAMPION_FIDE, RUNNERUP_FIDE}:
        raise ValueError(f"Unexpected playoff contestants {contestants}")
    top = max(score.values())
    leaders = {fid for fid, s in score.items() if abs(s - top) < 1e-9}
    if leaders != contestants:
        raise ValueError(f"Playoff contestants {contestants} are not the joint leaders {leaders}")

    po_score: defaultdict[str, float] = defaultdict(float)
    for g in playoff:
        h = g.headers
        if h["Result"] == "1-0":
            po_score[h["WhiteFideId"]] += 1.0
        elif h["Result"] == "0-1":
            po_score[h["BlackFideId"]] += 1.0
        else:
            po_score[h["WhiteFideId"]] += 0.5
            po_score[h["BlackFideId"]] += 0.5
    if po_score[CHAMPION_FIDE] <= po_score[RUNNERUP_FIDE]:
        raise ValueError(f"Playoff does not make {CHAMPION_FIDE} the winner: {dict(po_score)}")

    return {"byes": byes, "playoff": playoff, "swiss": swiss, "forfeits": forfeits,
            "score": dict(score), "played": dict(played), "real_games": real_games,
            "august": august, "changed": changed,
            "pgn_titles": {k: sorted(v) for k, v in pgn_titles.items()},
            "po_score": dict(po_score)}


def build() -> dict[str, object]:
    starters = load_starting_rank(STARTING_RANK)
    games = load_games(SOURCE_PGN)
    a = audit(starters, games)
    eco_entries = load_eco(ECO_TABLE)
    score, changed = a["score"], a["changed"]

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-ch-gbr-2024"})
    header = child(root, "header")
    child(header, "name", "British Chess Championship 2024")
    er = child(header, "eventRef", ref=EVENT_REF, source=RESULTS_URL)
    child(er, "name", "British Chess Championship 2024")
    child(er, "fideEventId", FIDE_EVENT_ID)
    child(header, "eventType", "swiss")
    child(header, "cadence", "classical")
    child(header, "federation", "ENG")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2024, m=7, d=27, iso="2024-07-27")
    child(child(dates, "end"), "day", y=2024, m=8, d=4, iso="2024-08-04")
    place = child(header, "placeRef", ref="place:admin2:GBR-50179", kind="admin2",
                  source="https://www.wikidata.org/wiki/Q128147")
    child(place, "name", "Kingston upon Hull")
    child(place, "country", "GBR")
    child(place, "admin1", "City of Kingston upon Hull")
    child(header, "venue", "Hull City Hall, Queen Victoria Square")
    organizers = child(header, "organizers")
    org = child(organizers, "organizer")
    child(org, "name", "English Chess Federation")
    child(org, "federation", "ENG")
    child(org, "role", "organizer")
    director = child(header, "tournamentDirector")
    child(director, "name", "Kevin Staveley")
    child(director, "role", "tournament director")
    child(director, "titleArbiter", "IO")
    arbiters = child(header, "arbiters")
    for arb_name, arb_role, arb_title in (
        ("Adrian Elwin", "chief arbiter", "IA"),
        ("Matthew Carr", "deputy chief arbiter", "IA"),
        ("Emma-Jane Billington-Phillips", "arbiter", "FA"),
    ):
        arb = child(arbiters, "arbiter")
        child(arb, "name", arb_name)
        child(arb, "role", arb_role)
        child(arb, "titleArbiter", arb_title)
    child(header, "rounds", ROUNDS)
    child(header, "pairingProgram", "Swiss-Manager from Heinz Herzog")

    participants = child(root, "participants")
    for s in sorted(starters.values(), key=lambda x: (-score[x.fide_id], x.seed)):
        part = child(participants, "participant", id=s.pid)
        ref = child(part, "playerRef", ref=f"player:fide:{s.fide_id}", source=RESULTS_URL)
        name = child(ref, "name", display=s.name)
        child(name, "family", s.family)
        if s.given:
            child(name, "given", s.given)
        child(ref, "federation", s.federation)
        title = s.title if s.title in VALID_TITLES else ""
        if not title:
            from_pgn = a["pgn_titles"].get(s.fide_id, [])
            title = from_pgn[0] if from_pgn else ""
        if title:
            child(ref, "title", title)
        child(child(ref, "ids"), "fideId", s.fide_id)
        if s.sex == "w":
            child(ref, "sex", "F")
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        snap = child(part, "ratingSnapshot", system="fide", scope="standard")
        child(snap, "value", s.rating)
        child(child(snap, "asOf"), "month", y=2024, m=7, raw="event starting rating (July 2024 FIDE list)")
        child(snap, "publishedForEvent", "true")
        child(part, "seed", s.seed)
        child(part, "score", decimal(score[s.fide_id]))
        if s.fide_id == CHAMPION_FIDE:
            child(part, "placement", 1)
        elif s.fide_id == RUNNERUP_FIDE:
            child(part, "placement", 2)
        bits = [f"Scored {decimal(score[s.fide_id])}/{ROUNDS} in the Swiss."]
        if s.fide_id in changed:
            bits.append(f"FIDE standard rating changed to {changed[s.fide_id]} on the August 2024 list, "
                        f"which took effect mid-event (from round 7); the snapshot above is the "
                        f"July 2024 rating the event started from.")
        if s.title and s.title not in VALID_TITLES:
            bits.append(f"The official starting rank lists the title {s.title!r}, which is not a FIDE "
                        f"over-the-board title in CTML's vocabulary, and the source PGN asserts no title "
                        f"for this player, so no title is recorded.")
        child(part, "notes", " ".join(bits))

    games_el = child(root, "games")
    clocked = eco_n = term_n = 0
    ordered = sorted((g for g in games if not is_bye(g)),
                     key=lambda g: (int(g.headers["Round"]), int(g.headers["Board"])))
    game_ids: dict[int, str] = {}
    for g in ordered:
        h = g.headers
        rnd = int(h["Round"])
        is_po = rnd in PLAYOFF_ROUNDS
        label = f"PO{rnd - ROUNDS}" if is_po else f"R{rnd}"
        gid = f"g-po{rnd - ROUNDS}" if is_po else f"g-r{rnd:02d}-b{int(h['Board']):03d}"
        game_ids[rnd] = gid
        ge = child(games_el, "game", id=gid, round=label, board=h["Board"],
                   white=f"p-fide-{h['WhiteFideId']}", black=f"p-fide-{h['BlackFideId']}",
                   result=h["Result"])
        nodes = list(g.mainline())
        code = classify_eco(tuple(n.move.uci() for n in nodes), eco_entries) if nodes else None
        if code:
            child(ge, "eco", code)
            eco_n += 1
        child(ge, "start", standard="true")
        raw_tc = h["TimeControl"]
        initial, inc = (int(x) for x in raw_tc.split("+"))
        cadence = "classical" if raw_tc == CLASSICAL_TC else ("rapid" if initial >= 600 else "blitz")
        tc = child(ge, "timeControl", cadence=cadence)
        child(tc, "raw", raw_tc)
        child(tc, "initialSeconds", initial)
        child(tc, "incrementSeconds", inc)
        if raw_tc == CLASSICAL_TC:
            child(tc, "note", TC_PROSE)
        if nodes:
            has = all(n.clock() is not None for n in nodes)
            moves = child(ge, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(has).lower())
            for n in nodes:
                attrs = {"ply": n.ply(), "value": n.move.uci()}
                c = n.clock()
                if c is not None:
                    attrs["clockSeconds"] = int(round(c))
                    clocked += 1
                child(moves, "move", **attrs)
        if h["Result"] == "0-0":
            child(ge, "termination", "forfeit")
            term_n += 1
        else:
            term = game_termination(g)
            if term:
                child(ge, "termination", term)
                term_n += 1
        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)
        src = child(ge, "source", kind="chess.com-pgn")
        child(src, "uri", SOURCE_PGN.as_uri())
        note = f"EndDate={h.get('EndDate', '')}; EndTime={h.get('EndTime', '')}."
        if is_po:
            leg_tc = "rapid 20+10" if cadence == "rapid" else "blitz 5+3"
            note = (f"Title playoff game {rnd - ROUNDS} of {len(PLAYOFF_ROUNDS)} ({leg_tc}). Elo tags "
                    f"here are the cadence-specific ratings, not the standard ratings recorded on the "
                    f"participants. ") + note
        elif h["Result"] == "0-0":
            note = ("Double forfeit: a scheduled pairing neither player played. The source records it "
                    "with the literal movetext \"0-0\" and both clocks untouched at 1:30:30. ") + note
        elif h["Date"] >= RATING_ROLLOVER:
            note = "Elo tags here are August 2024 FIDE list ratings; the list rolled over mid-event. " + note
        child(src, "note", note)

    non_games = child(games_el, "nonGames")
    for g in a["byes"]:
        h = g.headers
        child(non_games, "nonGame", participant=f"p-fide-{h['WhiteFideId']}",
              round=f"R{int(h['Round'])}", kind="bye-full")

    bracket = child(root, "bracket", kind="single-elimination")
    stage = child(bracket, "stage", name="title-playoff", order=1)
    tie = child(stage, "tie", winner=f"p-fide-{CHAMPION_FIDE}")
    for fid in (CHAMPION_FIDE, RUNNERUP_FIDE):
        child(tie, "side", competitor=f"p-fide-{fid}", score=decimal(a["po_score"][fid]),
              outcome="win" if fid == CHAMPION_FIDE else "loss")
    for i, rnd in enumerate(PLAYOFF_ROUNDS, start=1):
        g = next(x for x in a["playoff"] if int(x.headers["Round"]) == rnd)
        h = g.headers
        white_pts = 1.0 if h["Result"] == "1-0" else (0.0 if h["Result"] == "0-1" else 0.5)
        champ_pts = white_pts if h["WhiteFideId"] == CHAMPION_FIDE else 1.0 - white_pts
        child(tie, "leg", number=i, firstScore=decimal(champ_pts),
              secondScore=decimal(1.0 - champ_pts), game=game_ids[rnd])
    child(tie, "notes",
          "Title playoff after Gawain Jones and David Howell tied on 7/9. Two rapid games (20+10) were "
          "split 1-1, then two blitz games (5+3) went draw and Jones win, so Jones took the playoff "
          "2.5-1.5 and the 2024 British Championship.")

    notes = (
        f"British Chess Championship 2024, Championship section: an 86-player, 9-round classical Swiss "
        f"({TC_PROSE}) at Hull City Hall, Kingston upon Hull, 27 July - 4 August 2024, organised by the "
        f"English Chess Federation. Gawain Jones and David Howell tied on 7/9 and Jones won the four-game "
        f"rapid+blitz playoff 2.5-1.5 to take the title. The source holds {len(games)} records: "
        f"{a['real_games']} played Swiss games, 1 double forfeit, 2 byes, and 4 playoff games. The byes "
        f"(round 1 Madhavan, round 7 Atako) are ctml:nonGame kind=\"bye-full\" rather than games - the "
        f"source writes them as pseudo-games with Black=\"bye\" and no moves. The double forfeit "
        f"(round 9, Saunders-Larkin) is an ordinary game with result=\"0-0\" and termination=\"forfeit\": "
        f"the pairing and both players are known facts, only the play is missing. Its movetext in the "
        f"source is the literal string \"0-0\", which is not SAN; the loader tolerates that one shape and "
        f"nothing else. Scores are computed from the played games plus the byes and asserted to total "
        f"{a['real_games'] + len(a['byes'])} points. No official final crosstable was available for this "
        f"build, so tiebreak order is not invented: participant/score is asserted for all 86 players but "
        f"participant/placement only for the two the playoff decided (1 Jones, 2 Howell). Seed, "
        f"federation, sex and the event rating come from the official Swiss-Manager starting rank; the "
        f"PGN carries none of them. The FIDE rating list rolled over mid-event - {len(changed)} of the 86 "
        f"players' Elo tags change on {RATING_ROLLOVER.replace('.', '-')} (round 7) - so each "
        f"ratingSnapshot is the July 2024 rating the event started from, the builder asserts every "
        f"pre-rollover Elo tag matches it, and the August value is recorded in that participant's notes. "
        f"Player titles come from the starting rank where it gives one, otherwise from the PGN, and the "
        f"builder refuses to emit if the two contradict each other; seed 79's starting-rank title "
        f"\"AIM\" is an arena title outside CTML's PlayerTitleType and the PGN asserts none, so that "
        f"player is left untitled. The official arbiter list is truncated in the captured source "
        f"(\"FA Igor Doklesti ... All arbiters\"), so only the fully-named officials are recorded. ECO "
        f"codes are classified by longest UCI-prefix match. Identity is by FIDE id. No engine "
        f"evaluations were present in the source, so none are invented. Uses the CTML 2.1 model "
        f"(participant/placement, ctml:bracket, ctml:nonGame)."
    )
    child(root, "notes", notes)
    s1 = child(root, "source", kind="chess.com-pgn")
    child(s1, "uri", SOURCE_PGN.as_uri())
    child(s1, "note", f"{len(games)} records ({a['real_games']} played Swiss games + 1 double forfeit + "
                      f"2 byes + 4 playoff games); SHA-256 {sha256(SOURCE_PGN)}. Moves, clocks, FIDE ids "
                      f"and results authoritative.")
    s2 = child(root, "source", kind="chess-results-starting-rank")
    child(s2, "uri", RESULTS_URL)
    child(s2, "note", f"Official Swiss-Manager tournament page (FIDE-Event-ID {FIDE_EVENT_ID}): starting "
                      f"rank with seed, title, FIDE id, federation, rating and sex for all 86 players, "
                      f"plus organiser, officials, venue, round count and time control. Transcribed to "
                      f"{STARTING_RANK.name}; SHA-256 {sha256(STARTING_RANK)}.")
    s3 = child(root, "source", kind="eco-table")
    child(s3, "uri", ECO_TABLE.as_uri())
    child(s3, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as stream:
        stream.write(b"\n")

    return {"output": str(OUTPUT), "participants": len(starters), "records": len(games),
            "swiss_games": len(a["swiss"]), "played": a["real_games"], "forfeits": len(a["forfeits"]),
            "byes": len(a["byes"]), "playoff": len(a["playoff"]), "points": decimal(sum(score.values())),
            "eco_games": eco_n, "terminations": term_n, "clocked_plies": clocked,
            "rating_changes": len(changed), "champion": starters[CHAMPION_FIDE].name,
            "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT)}


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
