from __future__ import annotations

import hashlib
import struct
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn
import chess.polyglot

from ctml_build import child, classify_eco, fingerprints, load_eco, load_pgn, q, sha256



ROOT = Path(__file__).resolve().parents[1]
CLOCK_PGN = Path(r"D:\elysium\sources\twic\2026-menchik-memorial.pgn")
CHESS_RESULTS_PGN = Path(r"D:\elysium\sources\twic\1471927.pgn")
CHESS_RESULTS_TEXT = Path(r"D:\elysium\sources\twic\2026-menchik-memorial.txt")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260824-20260830_11th-menchik-memorial.ctml"

CHESS_RESULTS_URL = "https://chess-results.com/tnr1471927.aspx?lan=1"
EVENT_REF = "event:20260824-20260830-11th-menchik-memorial-london"
PLACE_REF = "place:city:GBR-50388"


def game_key(game: chess.pgn.Game) -> tuple[str, str, str, str]:
    headers = game.headers
    return headers["Round"], headers["Board"], headers["White"], headers["Black"]


@dataclass(frozen=True)
class Player:
    seed: int
    title: str
    name: str
    chess_results_id: str
    fide_id: str
    federation: str
    rating: int

    @property
    def participant_id(self) -> str:
        return f"p-fide-{self.fide_id}"


def load_roster(path: Path) -> list[Player]:
    players: list[Player] = []
    in_roster = False
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        if raw_line.strip() == "Starting rank list of players":
            in_roster = True
            continue
        if not in_roster:
            continue
        fields = [field.strip() for field in raw_line.split("\t") if field.strip()]
        if len(fields) == 7 and fields[0].isdigit() and fields[3].isdigit() and fields[4].isdigit():
            players.append(
                Player(
                    seed=int(fields[0]),
                    title=fields[1],
                    name=fields[2],
                    chess_results_id=fields[3],
                    fide_id=fields[4],
                    federation=fields[5],
                    rating=int(fields[6]),
                )
            )
    if len(players) != 10 or {player.seed for player in players} != set(range(1, 11)):
        raise ValueError(f"Expected a ten-player seed list, got {players!r}")
    return sorted(players, key=lambda player: player.seed)


def score_table(games: list[chess.pgn.Game]) -> dict[str, float]:
    scores: defaultdict[str, float] = defaultdict(float)
    for game in games:
        white = game.headers["White"]
        black = game.headers["Black"]
        result = game.headers["Result"]
        if result == "1-0":
            scores[white] += 1.0
        elif result == "0-1":
            scores[black] += 1.0
        elif result == "1/2-1/2":
            scores[white] += 0.5
            scores[black] += 0.5
        else:
            raise ValueError(f"Unsupported result {result!r} in {game_key(game)}")
    return dict(scores)


def add_partial_day(parent: ET.Element, local: str, iso_date: str) -> None:
    year, month, day = (int(part) for part in iso_date.split("-"))
    wrapper = child(parent, local)
    child(wrapper, "day", y=year, m=month, d=day, iso=iso_date)


def add_person_name(parent: ET.Element, display: str) -> None:
    name = child(parent, "name", display=display)
    if "," in display:
        family, given = (part.strip() for part in display.split(",", 1))
        child(name, "family", family)
        if given:
            child(name, "given", given)
    else:
        child(name, "unstructured", display)


def add_named_party(
    parent: ET.Element,
    local: str,
    name: str,
    role: str,
    fide_id: str,
    federation: str | None = None,
) -> None:
    party = child(parent, local)
    child(party, "name", name)
    if federation:
        child(party, "federation", federation)
    child(party, "role", role)
    ids = child(party, "ids")
    child(ids, "fideId", fide_id)


def game_termination(game: chess.pgn.Game) -> str | None:
    explicit = game.headers.get("Termination", "").strip().lower()
    if explicit:
        allowed = {
            "normal",
            "resignation",
            "checkmate",
            "timeout",
            "stalemate",
            "agreement",
            "adjudication",
            "forfeit",
            "abandoned",
            "unknown",
        }
        if explicit not in allowed:
            raise ValueError(f"Unknown termination {explicit!r} in {game_key(game)}")
        return explicit
    board = game.end().board()
    if board.is_checkmate():
        return "checkmate"
    if board.is_stalemate():
        return "stalemate"
    return None


def audit_sources(
    clock_games: list[chess.pgn.Game],
    chess_results_games: list[chess.pgn.Game],
    roster: list[Player],
) -> tuple[dict[tuple[str, str, str, str], chess.pgn.Game], list[tuple[object, ...]]]:
    if len(clock_games) != 45:
        raise ValueError(f"Expected 45 clock-rich games, got {len(clock_games)}")
    if len(chess_results_games) != 44:
        raise ValueError(f"Expected 44 ChessResults PGN games, got {len(chess_results_games)}")

    clock_map = {game_key(game): game for game in clock_games}
    chess_results_map = {game_key(game): game for game in chess_results_games}
    if len(clock_map) != len(clock_games) or len(chess_results_map) != len(chess_results_games):
        raise ValueError("Duplicate round/board/player game key")

    expected_missing = ("9", "4", "Hryshchenko, Kamila", "Schulze, Lara")
    if set(clock_map) - set(chess_results_map) != {expected_missing}:
        raise ValueError(f"Unexpected ChessResults game coverage: {set(clock_map) - set(chess_results_map)}")
    if set(chess_results_map) - set(clock_map):
        raise ValueError("ChessResults PGN contains a game absent from the clock-rich PGN")

    expected_truncation = ("3", "4", "Cosman, Andreea-Marioara", "Hryshchenko, Kamila")
    differing: list[tuple[str, str, str, str]] = []
    for key in sorted(chess_results_map):
        clock_moves = list(clock_map[key].mainline_moves())
        chess_results_moves = list(chess_results_map[key].mainline_moves())
        if clock_moves != chess_results_moves:
            differing.append(key)
            if key != expected_truncation or chess_results_moves != clock_moves[: len(chess_results_moves)]:
                raise ValueError(f"Unexpected movetext difference in {key}")
        if clock_map[key].headers["Result"] != chess_results_map[key].headers["Result"]:
            raise ValueError(f"Result disagreement in {key}")
    if differing != [expected_truncation]:
        raise ValueError(f"Unexpected differing games: {differing}")

    names = {player.name for player in roster}
    if {game.headers[color] for game in clock_games for color in ("White", "Black")} != names:
        raise ValueError("PGN player set does not match ChessResults roster")

    roster_by_name = {player.name: player for player in roster}
    for game in clock_games:
        for color in ("White", "Black"):
            player = roster_by_name[game.headers[color]]
            if game.headers[f"{color}FideId"] != player.fide_id:
                raise ValueError(f"FIDE ID disagreement for {player.name}")
            if int(game.headers[f"{color}Elo"]) != player.rating:
                raise ValueError(f"Rating disagreement for {player.name}")
            if game.headers[f"{color}Title"] != player.title:
                raise ValueError(f"Title disagreement for {player.name}")

    pairings = Counter(tuple(sorted((game.headers["White"], game.headers["Black"]))) for game in clock_games)
    if len(pairings) != 45 or any(count != 1 for count in pairings.values()):
        raise ValueError("The PGN is not a complete single round robin")
    for round_number in range(1, 10):
        round_games = [game for game in clock_games if int(game.headers["Round"]) == round_number]
        seats = [game.headers[color] for game in round_games for color in ("White", "Black")]
        if len(round_games) != 5 or Counter(seats) != Counter(names):
            raise ValueError(f"Incomplete or duplicate player coverage in round {round_number}")

    clock_issues: list[tuple[object, ...]] = []
    for game in clock_games:
        nodes = list(game.mainline())
        for node in nodes:
            clock = node.clock()
            if clock is None or not float(clock).is_integer() or clock < 0:
                raise ValueError(f"Missing/non-integral move clock at {game_key(game)}, ply {node.ply()}")
        for color, parity in (("White", 1), ("Black", 0)):
            side_nodes = [node for node in nodes if node.ply() % 2 == parity]
            if side_nodes:
                hours, minutes, seconds = (int(part) for part in game.headers[f"{color}Clock"].split(":"))
                header_clock = hours * 3600 + minutes * 60 + seconds
                last_move_clock = int(side_nodes[-1].clock())
                if header_clock != last_move_clock:
                    clock_issues.append((game.headers["Round"], game.headers["Board"], color, last_move_clock, header_clock))

    return chess_results_map, clock_issues


def build() -> dict[str, object]:
    clock_games = load_pgn(CLOCK_PGN)
    chess_results_games = load_pgn(CHESS_RESULTS_PGN)
    roster = load_roster(CHESS_RESULTS_TEXT)
    chess_results_map, clock_issues = audit_sources(clock_games, chess_results_games, roster)
    eco_entries = load_eco(ECO_TABLE)
    scores = score_table(clock_games)
    if sum(scores.values()) != 45.0:
        raise ValueError(f"Score sum must be 45.0, got {sum(scores.values())}")

    root = ET.Element(
        q("tournament"),
        {"ctmlVersion": "2.0", "id": "tournament-cr-1471927"},
    )
    header = child(root, "header")
    child(header, "name", "11th Menchik Memorial")
    event_ref = child(header, "eventRef", ref=EVENT_REF, source=CHESS_RESULTS_URL)
    child(event_ref, "name", "11th Menchik Memorial")
    child(header, "eventType", "round-robin")
    child(header, "cadence", "classical")
    child(header, "federation", "ENG")
    dates = child(header, "dates")
    add_partial_day(dates, "start", "2026-08-24")
    add_partial_day(dates, "end", "2026-08-30")
    place = child(header, "placeRef", ref=PLACE_REF, kind="city", source="https://www.wikidata.org/wiki/Q84")
    child(place, "name", "London")
    child(place, "country", "GBR")
    child(place, "city", "London")
    child(header, "venue", "Hammersmith")

    organizers = child(header, "organizers")
    add_named_party(
        organizers,
        "organizer",
        "Milewska, Agnieszka",
        "organizer and tournament director",
        "1196391",
        "ENG",
    )
    arbiters = child(header, "arbiters")
    add_named_party(arbiters, "arbiter", "McKeown, Paul", "chief arbiter", "2501287", "IRL")
    add_named_party(arbiters, "arbiter", "Boozorginia, Zoya", "deputy chief arbiter", "343093964")

    participants = child(root, "participants")
    roster_by_name = {player.name: player for player in roster}
    for player in roster:
        participant = child(participants, "participant", id=player.participant_id)
        player_ref = child(
            participant,
            "playerRef",
            ref=f"player:fide:{player.fide_id}",
            source=CHESS_RESULTS_URL,
        )
        add_person_name(player_ref, player.name)
        child(player_ref, "federation", player.federation)
        child(player_ref, "title", player.title)
        ids = child(player_ref, "ids")
        child(ids, "fideId", player.fide_id)
        child(ids, "internalId", f"chess-results:{player.chess_results_id}")
        child(player_ref, "resolution", method="fide-id", resolver="ctml-menchik-builder/1")

        rating = child(participant, "ratingSnapshot", system="fide", scope="standard")
        child(rating, "value", player.rating)
        as_of = child(rating, "asOf")
        child(as_of, "month", y=2026, m=8, raw="event starting rating")
        child(rating, "publishedForEvent", "true")
        child(participant, "seed", player.seed)
        score = scores[player.name]
        child(participant, "score", str(int(score)) if score.is_integer() else str(score))

    games_element = child(root, "games")
    clock_uri = CLOCK_PGN.as_uri()
    chess_results_pgn_uri = CHESS_RESULTS_PGN.as_uri()
    clocked_plies = 0
    eco_count = 0
    termination_count = 0
    for game in sorted(clock_games, key=lambda item: (int(item.headers["Round"]), int(item.headers["Board"]))):
        headers = game.headers
        key = game_key(game)
        nodes = list(game.mainline())
        game_element = child(
            games_element,
            "game",
            id=f"g-r{int(headers['Round']):02d}-b{int(headers['Board']):02d}",
            round=headers["Round"],
            board=headers["Board"],
            white=roster_by_name[headers["White"]].participant_id,
            black=roster_by_name[headers["Black"]].participant_id,
            result=headers["Result"],
        )
        eco = classify_eco(tuple(m.uci() for m in game.mainline_moves()), eco_entries)
        if eco:
            child(game_element, "eco", eco)
            eco_count += 1
        child(game_element, "start", standard="true")
        time_control = child(game_element, "timeControl", cadence="classical")
        child(time_control, "raw", headers["TimeControl"])
        child(time_control, "initialSeconds", 5400)
        child(time_control, "incrementSeconds", 30)

        moves = child(
            game_element,
            "moves",
            notation="uci",
            plyCount=len(nodes),
            clockInfo=str(bool(nodes)).lower(),
        )
        for node in nodes:
            clock = int(node.clock())
            child(moves, "move", ply=node.ply(), value=node.move.uci(), clockSeconds=clock)
            clocked_plies += 1

        termination = game_termination(game)
        if termination:
            child(game_element, "termination", termination)
            termination_count += 1

        trajectory, final_position = fingerprints(game)
        fingerprint_set = child(game_element, "fingerprints")
        child(
            fingerprint_set,
            "fingerprint",
            scheme="zobrist-polyglot-1",
            scope="trajectory",
            value=trajectory,
        )
        child(
            fingerprint_set,
            "fingerprint",
            scheme="zobrist-polyglot-1",
            scope="finalPosition",
            value=final_position,
        )

        clock_source = child(game_element, "source", kind="chess.com-pgn")
        child(clock_source, "uri", clock_uri)
        source_note = (
            f"PGN Date={headers['Date']}; EndDate={headers['EndDate']}; EndTime={headers['EndTime']}; "
            f"WhiteClock={headers['WhiteClock']}; BlackClock={headers['BlackClock']}. "
            "Per-move %clk values are stored as clockSeconds."
        )
        child(clock_source, "note", source_note)

        if key in chess_results_map:
            corroborating = child(game_element, "source", kind="chess-results-pgn")
            child(corroborating, "uri", chess_results_pgn_uri)
            if key == ("3", "4", "Cosman, Andreea-Marioara", "Hryshchenko, Kamila"):
                child(
                    corroborating,
                    "note",
                    "ChessResults movetext ends at ply 73; the clock-rich PGN continues consistently through ply 79 and is used here.",
                )

    notes = (
        "Complete nine-round single round robin: 10 participants, 45 pairings, and no byes. "
        "Starting ratings average 2211; the event was submitted for national and international rating. "
        "The clock-rich PGN supplies all 3,855 played plies and a clock value after every ply. "
        "The ChessResults PGN has 44 games: round 3 board 4 is truncated after ply 73, and the zero-ply "
        "round 9 board 4 forfeit is absent. The richer PGN is authoritative in both cases. "
        "ECO codes were derived by longest UCI-prefix match against all.tsv; the forfeit has no ECO. "
        "No engine evaluations were present in the supplied sources, so no eval elements were invented."
    )
    child(root, "notes", notes)

    sources = [
        (
            "chess.com-pgn",
            CLOCK_PGN,
            f"45 games; SHA-256 {sha256(CLOCK_PGN)}. Move clocks are authoritative. "
            f"Four PGN header final-clock values differ from the last corresponding %clk annotation: {clock_issues!r}.",
        ),
        (
            "chess-results-pgn",
            CHESS_RESULTS_PGN,
            f"44 games; SHA-256 {sha256(CHESS_RESULTS_PGN)}. Used as independent movetext/result corroboration.",
        ),
        (
            "chess-results-metadata",
            CHESS_RESULTS_TEXT,
            f"Official event, organizer, arbiter, roster, rating, and scheduling metadata; SHA-256 {sha256(CHESS_RESULTS_TEXT)}. "
            "Source states last update 2026-09-01 01:28:39.",
        ),
        (
            "eco-table",
            ECO_TABLE,
            f"Opening classification table; SHA-256 {sha256(ECO_TABLE)}.",
        ),
    ]
    for kind, path, note in sources:
        source = child(root, "source", kind=kind)
        child(source, "uri", path.as_uri())
        child(source, "note", note)

    ET.indent(root, space="  ")
    tree = ET.ElementTree(root)
    tree.write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as stream:
        stream.write(b"\n")

    return {
        "output": str(OUTPUT),
        "participants": len(roster),
        "games": len(clock_games),
        "plies": sum(len(list(game.mainline())) for game in clock_games),
        "clocked_plies": clocked_plies,
        "eco_games": eco_count,
        "terminations": termination_count,
        "chess_results_games": len(chess_results_games),
        "clock_header_mismatches": len(clock_issues),
        "bytes": OUTPUT.stat().st_size,
        "sha256": sha256(OUTPUT),
    }


if __name__ == "__main__":
    for key, value in build().items():
        print(f"{key}={value}")
