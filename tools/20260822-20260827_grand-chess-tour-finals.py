from __future__ import annotations

import hashlib
import struct
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn
import chess.polyglot

from ctml_build import child, classify_eco, fingerprints, game_termination, load_eco, load_pgn, q, sha256



ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2026-gct-finals.pgn")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260822-20260827_grand-chess-tour-finals.ctml"

# ---------------------------------------------------------------------------
# Player metadata.
#
# The chess.com PGN carries White/Black, WhiteFideId/BlackFideId, WhiteElo/
# BlackElo (live, per game) and WhiteTitle/BlackTitle, but NO federation and
# no structured given/family split for Praggnanandhaa. Federation is resolved
# from the (in-source) FIDE id -- an authoritative lookup, not a guess -- and
# recorded as such. FIDE ids and titles below are asserted against every game
# header at build time (see audit_players).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Player:
    pgn_name: str
    display: str
    fide_id: str
    federation: str
    title: str
    # Structured name parts, or None to emit <unstructured> (Praggnanandhaa R).
    family: str | None
    given: str | None

    @property
    def participant_id(self) -> str:
        return f"p-fide-{self.fide_id}"


PLAYERS = [
    Player("Praggnanandhaa R", "Praggnanandhaa R", "25059530", "IND", "GM", None, None),
    Player("Caruana, Fabiano", "Caruana, Fabiano", "2020009", "USA", "GM", "Caruana", "Fabiano"),
    Player("So, Wesley", "So, Wesley", "5202213", "USA", "GM", "So", "Wesley"),
    Player("Keymer, Vincent", "Keymer, Vincent", "12940690", "GER", "GM", "Keymer", "Vincent"),
]
PLAYER_BY_PGN = {p.pgn_name: p for p in PLAYERS}

# Final placement (from the weighted match results, audited below).
PLACEMENT = {
    "Praggnanandhaa R": 1,
    "Caruana, Fabiano": 2,
    "So, Wesley": 3,
    "Keymer, Vincent": 4,
}

# (round-major, board) -> (match id, stage label). Verified against the pairs
# of players actually seen on each board.
MATCH_OF = {
    ("1", "1"): ("SF-A", "semifinal"),
    ("1", "2"): ("SF-B", "semifinal"),
    ("2", "1"): ("FINAL", "final"),
    ("2", "2"): ("THIRD", "third-place"),
}
EXPECTED_MATCH_PAIRS = {
    "SF-A": {"Keymer, Vincent", "Praggnanandhaa R"},
    "SF-B": {"So, Wesley", "Caruana, Fabiano"},
    "FINAL": {"Caruana, Fabiano", "Praggnanandhaa R"},
    "THIRD": {"Keymer, Vincent", "So, Wesley"},
}
# Weighted match totals published in the official bracket (winner, loser).
EXPECTED_MATCH_SCORES = {
    "SF-A": {"Praggnanandhaa R": 19, "Keymer, Vincent": 9},
    "SF-B": {"Caruana, Fabiano": 18, "So, Wesley": 12},
    "FINAL": {"Praggnanandhaa R": 15, "Caruana, Fabiano": 13},
    "THIRD": {"So, Wesley": 18, "Keymer, Vincent": 12},
}
# Points by cadence: (win, draw). Loss is 0. The classical-tie bonus blitz
# game scores as an ordinary blitz game.
CADENCE_POINTS = {"classical": (6, 3), "rapid": (4, 2), "blitz": (2, 1)}

# The event was captured from a local chess.com broadcast PGN; no canonical
# public event URL was recorded, so provenance points at the source file
# rather than a guessed web address.
SITE_SOURCE = SOURCE_PGN.as_uri()


def parse_time_control(raw: str) -> tuple[int, int]:
    initial, increment = (int(part) for part in raw.split("+"))
    return initial, increment


def cadence_of(initial_seconds: int) -> str:
    if initial_seconds >= 3600:
        return "classical"
    if initial_seconds >= 600:
        return "rapid"
    return "blitz"


CADENCE_SCOPE = {"classical": "standard", "rapid": "rapid", "blitz": "blitz"}


def round_parts(game: chess.pgn.Game) -> tuple[str, str]:
    major, minor = game.headers["Round"].split(".", 1)
    return major, minor


def match_id_of(game: chess.pgn.Game) -> tuple[str, str]:
    major, _ = round_parts(game)
    return MATCH_OF[(major, game.headers["Board"])]


def audit_players(games: list[chess.pgn.Game]) -> None:
    for game in games:
        for color in ("White", "Black"):
            name = game.headers[color]
            player = PLAYER_BY_PGN.get(name)
            if player is None:
                raise ValueError(f"Unknown player {name!r}")
            if game.headers[f"{color}FideId"] != player.fide_id:
                raise ValueError(f"FIDE id disagreement for {name} in round {game.headers['Round']}")
            if game.headers[f"{color}Title"] != player.title:
                raise ValueError(f"Title disagreement for {name}")
    seen = {game.headers[color] for game in games for color in ("White", "Black")}
    if seen != set(PLAYER_BY_PGN):
        raise ValueError(f"Player set mismatch: {seen}")


def match_bonus_blitz(match_games: list[chess.pgn.Game]) -> chess.pgn.Game | None:
    """The classical-tie decider: a blitz game played before any rapid game."""
    seen_rapid = False
    for game in match_games:
        initial, _ = parse_time_control(game.headers["TimeControl"])
        cadence = cadence_of(initial)
        if cadence == "rapid":
            seen_rapid = True
        elif cadence == "blitz" and not seen_rapid:
            return game
    return None


def compute_match_scores(games: list[chess.pgn.Game]) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for game in games:
        match_id, _ = match_id_of(game)
        initial, _ = parse_time_control(game.headers["TimeControl"])
        cadence = cadence_of(initial)
        win, draw = CADENCE_POINTS[cadence]
        white, black = game.headers["White"], game.headers["Black"]
        result = game.headers["Result"]
        if result == "1-0":
            scores[match_id][white] += win
        elif result == "0-1":
            scores[match_id][black] += win
        elif result == "1/2-1/2":
            scores[match_id][white] += draw
            scores[match_id][black] += draw
        else:
            raise ValueError(f"Unsupported result {result!r}")
    return {mid: dict(table) for mid, table in scores.items()}


def audit_matches(games: list[chess.pgn.Game], match_scores: dict[str, dict[str, float]]) -> None:
    pairs: dict[str, set[str]] = defaultdict(set)
    for game in games:
        match_id, _ = match_id_of(game)
        pairs[match_id].update((game.headers["White"], game.headers["Black"]))
    for match_id, expected in EXPECTED_MATCH_PAIRS.items():
        if pairs[match_id] != expected:
            raise ValueError(f"Match {match_id} pairing mismatch: {pairs[match_id]}")
    for match_id, expected in EXPECTED_MATCH_SCORES.items():
        got = {name: int(value) if float(value).is_integer() else value for name, value in match_scores[match_id].items()}
        if got != expected:
            raise ValueError(f"Match {match_id} weighted score {got} != published bracket {expected}")


def add_person_name(parent: ET.Element, player: Player) -> None:
    name = child(parent, "name", display=player.display)
    if player.family:
        child(name, "family", player.family)
        if player.given:
            child(name, "given", player.given)
    else:
        child(name, "unstructured", player.display)


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    if len(games) != 34:
        raise ValueError(f"Expected 34 games, got {len(games)}")
    audit_players(games)

    # First-observed live rating per (player, cadence), in file order.
    ratings: dict[tuple[str, str], int] = {}
    for game in games:
        initial, _ = parse_time_control(game.headers["TimeControl"])
        cadence = cadence_of(initial)
        for color in ("White", "Black"):
            key = (game.headers[color], cadence)
            if key not in ratings:
                ratings[key] = int(game.headers[f"{color}Elo"])

    match_scores = compute_match_scores(games)
    audit_matches(games, match_scores)

    # Identify each match's bonus-blitz game (if any) for segment tagging.
    by_match: dict[str, list[chess.pgn.Game]] = defaultdict(list)
    for game in sorted(games, key=lambda g: (int(round_parts(g)[0]), int(round_parts(g)[1]))):
        by_match[match_id_of(game)[0]].append(game)
    bonus_games = {id(match_bonus_blitz(ms)) for ms in by_match.values()}
    bonus_games.discard(id(None))

    eco_entries = load_eco(ECO_TABLE)

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-gct-finals-2026"})
    header = child(root, "header")
    child(header, "name", "2026 Grand Chess Tour Finals")
    event_ref = child(
        header, "eventRef",
        ref="event:20260822-20260827-2026-grand-chess-tour-finals-saint-louis",
        source=SITE_SOURCE,
    )
    child(event_ref, "name", "2026 Grand Chess Tour Finals")
    child(header, "eventType", "knockout")
    child(header, "cadence", "mixed")
    child(header, "federation", "USA")
    dates = child(header, "dates")
    start = child(dates, "start")
    child(start, "day", y=2026, m=8, d=22, iso="2026-08-22")
    end = child(dates, "end")
    child(end, "day", y=2026, m=8, d=27, iso="2026-08-27")
    place = child(header, "placeRef", ref="place:city:USA-saint-louis-mo", kind="city")
    child(place, "name", "Saint Louis")
    child(place, "country", "USA")
    child(place, "admin1", "Missouri")
    child(place, "city", "Saint Louis")
    child(header, "venue", "Saint Louis Chess Club")
    organizers = child(header, "organizers")
    organizer = child(organizers, "organizer")
    child(organizer, "name", "Saint Louis Chess Club")
    child(organizer, "role", "organizer")

    participants = child(root, "participants")
    for player in sorted(PLAYERS, key=lambda p: PLACEMENT[p.pgn_name]):
        participant = child(participants, "participant", id=player.participant_id)
        player_ref = child(participant, "playerRef", ref=f"player:fide:{player.fide_id}", source=SITE_SOURCE)
        add_person_name(player_ref, player)
        child(player_ref, "federation", player.federation)
        child(player_ref, "title", player.title)
        ids = child(player_ref, "ids")
        child(ids, "fideId", player.fide_id)
        child(player_ref, "resolution", method="fide-id", resolver="ctml-gct-finals-builder/1")
        for cadence in ("classical", "rapid", "blitz"):
            value = ratings.get((player.pgn_name, cadence))
            if value is None:
                continue
            snapshot = child(participant, "ratingSnapshot", system="fide", scope=CADENCE_SCOPE[cadence])
            child(snapshot, "value", value)
            as_of = child(snapshot, "asOf")
            child(as_of, "month", y=2026, m=8, raw=f"first observed live {cadence} rating")
            child(snapshot, "publishedForEvent", "false")
        placement = PLACEMENT[player.pgn_name]
        ordinal = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}[placement]
        paths = {
            "Praggnanandhaa R": "Won SF-A over Keymer 19-9, then the final over Caruana 15-13. Tour champion.",
            "Caruana, Fabiano": "Won SF-B over So 18-12, then lost the final to Praggnanandhaa 13-15.",
            "So, Wesley": "Lost SF-B to Caruana 12-18, then won the third-place match over Keymer 18-12.",
            "Keymer, Vincent": "Lost SF-A to Praggnanandhaa 9-19, then lost the third-place match to So 12-18.",
        }
        child(participant, "placement", placement)
        child(participant, "notes", f"Final placement: {ordinal}. {paths[player.pgn_name]}")

    games_element = child(root, "games")
    source_uri = SOURCE_PGN.as_uri()
    clocked_plies = 0
    eco_count = 0
    termination_count = 0
    for game in sorted(games, key=lambda g: (int(round_parts(g)[0]), int(round_parts(g)[1]), int(g.headers["Board"]))):
        headers = game.headers
        major, minor = round_parts(game)
        match_id, stage = match_id_of(game)
        initial, increment = parse_time_control(headers["TimeControl"])
        cadence = cadence_of(initial)
        is_bonus = id(game) in bonus_games
        segment = "blitz-bonus" if is_bonus else cadence
        nodes = list(game.mainline())

        white = PLAYER_BY_PGN[headers["White"]]
        black = PLAYER_BY_PGN[headers["Black"]]
        game_element = child(
            games_element, "game",
            id=f"g-r{major}-{minor}-b{headers['Board']}",
            round=headers["Round"], board=headers["Board"],
            white=white.participant_id, black=black.participant_id,
            result=headers["Result"],
        )
        eco = classify_eco(tuple(m.uci() for m in game.mainline_moves()), eco_entries)
        if eco:
            child(game_element, "eco", eco)
            eco_count += 1
        child(game_element, "start", standard="true")
        time_control = child(game_element, "timeControl", cadence=cadence)
        child(time_control, "raw", headers["TimeControl"])
        child(time_control, "initialSeconds", initial)
        child(time_control, "incrementSeconds", increment)

        moves = child(game_element, "moves", notation="uci", plyCount=len(nodes), clockInfo=str(bool(nodes)).lower())
        for node in nodes:
            clock = node.clock()
            if clock is None:
                raise ValueError(f"Missing clock at round {headers['Round']} board {headers['Board']} ply {node.ply()}")
            child(moves, "move", ply=node.ply(), value=node.move.uci(), clockSeconds=int(round(clock)))
            clocked_plies += 1

        termination = game_termination(game)
        if termination:
            child(game_element, "termination", termination)
            termination_count += 1

        # Per-game cadence phase; the match/stage grouping is now carried
        # structurally by the bracket below, so only segment stays a tag
        # (it also distinguishes the classical-tie blitz-bonus game).
        tags = child(game_element, "tags")
        child(tags, "tag", f"segment:{segment}")

        trajectory, final_position = fingerprints(game)
        fingerprint_set = child(game_element, "fingerprints")
        child(fingerprint_set, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=trajectory)
        child(fingerprint_set, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final_position)

        source = child(game_element, "source", kind="chess.com-pgn")
        child(source, "uri", source_uri)
        note = (
            f"PGN Date={headers['Date']}; EndDate={headers.get('EndDate', '')}; EndTime={headers.get('EndTime', '')}; "
            f"WhiteClock={headers.get('WhiteClock', '')}; BlackClock={headers.get('BlackClock', '')}. "
            "Per-move %clk values are stored as clockSeconds. Elo values are live per-game ratings."
        )
        child(source, "note", note)

    def fmt(score: float) -> str:
        return str(int(score)) if float(score).is_integer() else str(score)

    # ---- bracket (CTML 2.1) : stages -> ties -> legs (one per game) ----
    # Leg firstScore/secondScore are the WEIGHTED points that game contributed
    # to the winner/loser side, so the legs sum to each side's match points.
    bracket = child(root, "bracket", kind="single-elimination")
    for stage_name, stage_order, match_ids in (("semifinal", 1, ["SF-A", "SF-B"]),
                                               ("third-place", 2, ["THIRD"]),
                                               ("final", 3, ["FINAL"])):
        stage_el = child(bracket, "stage", name=stage_name, order=stage_order)
        for match_id in match_ids:
            table = match_scores[match_id]
            winner_name = max(table, key=table.get)
            loser_name = min(table, key=table.get)
            winner, loser = PLAYER_BY_PGN[winner_name], PLAYER_BY_PGN[loser_name]
            tie = child(stage_el, "tie", winner=winner.participant_id)
            child(tie, "side", competitor=winner.participant_id, score=fmt(table[winner_name]), outcome="win")
            child(tie, "side", competitor=loser.participant_id, score=fmt(table[loser_name]), outcome="loss")
            first_sum = second_sum = 0
            for leg_no, game in enumerate(by_match[match_id], start=1):
                initial, _ = parse_time_control(game.headers["TimeControl"])
                win, draw = CADENCE_POINTS[cadence_of(initial)]
                result = game.headers["Result"]
                if result == "1/2-1/2":
                    first_pts = second_pts = draw
                else:
                    game_winner = game.headers["White"] if result == "1-0" else game.headers["Black"]
                    first_pts = win if game_winner == winner_name else 0
                    second_pts = win if game_winner == loser_name else 0
                first_sum += first_pts
                second_sum += second_pts
                major, minor = round_parts(game)
                child(tie, "leg", number=leg_no, firstScore=first_pts, secondScore=second_pts,
                      game=f"g-r{major}-{minor}-b{game.headers['Board']}")
            if first_sum != table[winner_name] or second_sum != table[loser_name]:
                raise ValueError(f"{match_id}: legs sum {first_sum}-{second_sum} != match points "
                                 f"{table[winner_name]}-{table[loser_name]}")

    notes = (
        "Four-player single-elimination knockout at the Saint Louis Chess Club, 22-27 August 2026, "
        "played strictly from the chess.com broadcast (no ChessResults record was located). "
        "Bracket: semifinals SF-A (Keymer vs Praggnanandhaa) and SF-B (So vs Caruana) on 22-24 Aug; "
        "the final (Caruana vs Praggnanandhaa) and third-place match (Keymer vs So) on 25-27 Aug. "
        "Each match is two classical games, two rapid, and four blitz, scored classical 6/3, rapid 4/2, blitz 2/1 "
        "(win/draw); when both classical games are drawn the players contest a bonus blitz game (tagged "
        "segment:blitz-bonus), which scores as an ordinary blitz game. "
        f"Weighted match results: SF-A Praggnanandhaa {fmt(match_scores['SF-A']['Praggnanandhaa R'])}"
        f"-{fmt(match_scores['SF-A']['Keymer, Vincent'])} Keymer; "
        f"SF-B Caruana {fmt(match_scores['SF-B']['Caruana, Fabiano'])}"
        f"-{fmt(match_scores['SF-B']['So, Wesley'])} So; "
        f"final Praggnanandhaa {fmt(match_scores['FINAL']['Praggnanandhaa R'])}"
        f"-{fmt(match_scores['FINAL']['Caruana, Fabiano'])} Caruana; "
        f"third place So {fmt(match_scores['THIRD']['So, Wesley'])}"
        f"-{fmt(match_scores['THIRD']['Keymer, Vincent'])} Keymer. "
        "Final standings: 1 Praggnanandhaa, 2 Caruana, 3 So, 4 Keymer. "
        "Player federations were resolved from the in-source FIDE ids (not present in the chess.com PGN). "
        "The knockout is represented with the CTML 2.1 bracket model (stages -> ties -> legs, one leg per game) "
        "and participant placement; each game also carries a segment tag (classical/rapid/blitz/blitz-bonus). Each "
        "leg's scores are the weighted points that game contributed, so a tie's legs sum to its match points. "
        "Every played game, move, and per-move clock is stored losslessly. No engine evaluations were present "
        "in the source, so no eval elements were invented."
    )
    child(root, "notes", notes)

    source = child(root, "source", kind="chess.com-pgn")
    child(source, "uri", source_uri)
    child(source, "note",
          f"34 games; SHA-256 {sha256(SOURCE_PGN)}. Sole source: chess.com broadcast PGN with per-move %clk clocks. "
          "Move clocks and results are authoritative.")
    eco_source = child(root, "source", kind="eco-table")
    child(eco_source, "uri", ECO_TABLE.as_uri())
    child(eco_source, "note", f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as stream:
        stream.write(b"\n")

    return {
        "output": str(OUTPUT),
        "participants": len(PLAYERS),
        "games": len(games),
        "plies": clocked_plies,
        "eco_games": eco_count,
        "terminations": termination_count,
        "bytes": OUTPUT.stat().st_size,
        "sha256": sha256(OUTPUT),
    }


if __name__ == "__main__":
    for key, value in build().items():
        print(f"{key}={value}")
