from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess
import chess.pgn
import chess.polyglot

from ctml_build import child, classify_eco, fide_federations, fingerprints, game_termination, load_eco, load_pgn, q, sha256



ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\2026-esports-world-cup-playoffs.pgn")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260814-20260815_2026-esports-world-cup-playoffs.ctml"

# ---------------------------------------------------------------------------
# Player metadata.
#
# The chess.com PGN carries White/Black, WhiteFideId/BlackFideId, WhiteElo/
# BlackElo (live, per game) and WhiteTitle/BlackTitle, but NO federation and
# no structured given/family split for "Nihal Sarin" or "Erigaisi Arjun".
# Federations are resolved from the (in-source) FIDE ids against the official
# FIDE standard rating list -- an authoritative lookup, not a guess -- and
# audited against the values below at build time.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Player:
    pgn_name: str
    display: str
    fide_id: str
    federation: str
    title: str
    family: str
    given: str

    @property
    def participant_id(self) -> str:
        return f"p-fide-{self.fide_id}"


PLAYERS = [
    Player("Carlsen, Magnus",     "Carlsen, Magnus",     "1503014",  "NOR", "GM", "Carlsen",     "Magnus"),
    Player("Lazavik, Denis",      "Lazavik, Denis",      "13515110", "BLR", "GM", "Lazavik",     "Denis"),
    Player("Nakamura, Hikaru",    "Nakamura, Hikaru",    "2016192",  "USA", "GM", "Nakamura",    "Hikaru"),
    Player("Firouzja, Alireza",   "Firouzja, Alireza",   "12573981", "FRA", "GM", "Firouzja",    "Alireza"),
    Player("Nihal Sarin",         "Nihal Sarin",         "25092340", "IND", "GM", "Sarin",       "Nihal"),
    Player("Niemann, Hans Moke",  "Niemann, Hans Moke",  "2093596",  "USA", "GM", "Niemann",     "Hans Moke"),
    Player("Erigaisi Arjun",      "Erigaisi Arjun",      "35009192", "IND", "GM", "Erigaisi",    "Arjun"),
    Player("Abdusattorov, Nodirbek", "Abdusattorov, Nodirbek", "14204118", "UZB", "GM", "Abdusattorov", "Nodirbek"),
]
PLAYER_BY_PGN = {p.pgn_name: p for p in PLAYERS}

# Final placement. 1-4 are decided by the bracket; the four quarterfinal
# losers are officially tied 5th-8th on the chess.com bracket, so they all
# share placement 5.
PLACEMENT = {
    "Carlsen, Magnus":         1,
    "Lazavik, Denis":          2,
    "Nakamura, Hikaru":        3,
    "Firouzja, Alireza":       4,
    "Nihal Sarin":             5,
    "Niemann, Hans Moke":      5,
    "Erigaisi Arjun":          5,
    "Abdusattorov, Nodirbek":  5,
}

# Round major -> ordered list of (match_id, stage, {pgn_name_a, pgn_name_b}).
# Round 1: four quarterfinal ties, distinguished only by the players on them
# (round.game numbers in the PGN restart from 1 each match). Round 2:
# semifinals. Round 3: 3.1 runs the third-place match (Nakamura vs Firouzja)
# alongside the first final match (Carlsen vs Lazavik); 3.2 is the second
# final match. Both final matches together form one tie in the "final" stage.
QF_MATCHES = [
    ("QF-1", {"Carlsen, Magnus", "Nihal Sarin"}),
    ("QF-2", {"Firouzja, Alireza", "Niemann, Hans Moke"}),
    ("QF-3", {"Nakamura, Hikaru", "Erigaisi Arjun"}),
    ("QF-4", {"Abdusattorov, Nodirbek", "Lazavik, Denis"}),
]
SF_MATCHES = [
    ("SF-1", {"Carlsen, Magnus", "Firouzja, Alireza"}),
    ("SF-2", {"Nakamura, Hikaru", "Lazavik, Denis"}),
]
THIRD_MATCH = ("THIRD", {"Nakamura, Hikaru", "Firouzja, Alireza"})
FINAL_MATCH = ("FINAL", {"Carlsen, Magnus", "Lazavik, Denis"})

# Published bracket scores (chess.com's playoff diagram). Match points: 1 for
# a win, 0.5 for a draw. Asserted below against the games actually seen.
EXPECTED_MATCH_SCORES = {
    "QF-1":  {"Carlsen, Magnus": 2.5,      "Nihal Sarin": 0.5},
    "QF-2":  {"Firouzja, Alireza": 2.5,    "Niemann, Hans Moke": 1.5},
    "QF-3":  {"Nakamura, Hikaru": 3,       "Erigaisi Arjun": 2},
    "QF-4":  {"Lazavik, Denis": 3,         "Abdusattorov, Nodirbek": 2},
    "SF-1":  {"Carlsen, Magnus": 4,        "Firouzja, Alireza": 1},
    "SF-2":  {"Lazavik, Denis": 3.5,       "Nakamura, Hikaru": 1.5},
    "THIRD": {"Nakamura, Hikaru": 4,       "Firouzja, Alireza": 1},
    # Final is played across two 4-game matches (each shown as 3-1 on the
    # bracket); aggregate 6-2 for Carlsen.
    "FINAL": {"Carlsen, Magnus": 6,        "Lazavik, Denis": 2},
}

# Bracket order: quarterfinals, semifinals, third-place, final.
STAGE_PLAN = [
    ("quarterfinal", 1, [m[0] for m in QF_MATCHES]),
    ("semifinal",    2, [m[0] for m in SF_MATCHES]),
    ("third-place",  3, [THIRD_MATCH[0]]),
    ("final",        4, [FINAL_MATCH[0]]),
]

# Provenance: the chess.com broadcast PGN. No published event URL was
# captured with the source, so provenance points at the local file.
SITE_SOURCE = SOURCE_PGN.as_uri()


def parse_time_control(raw: str) -> tuple[int, int]:
    if "+" in raw:
        initial, increment = (int(part) for part in raw.split("+"))
    else:
        initial, increment = int(raw), 0
    return initial, increment


def match_id_of(game: chess.pgn.Game) -> tuple[str, str]:
    major, _, _ = game.headers["Round"].split(".", 2)
    pair = {game.headers["White"], game.headers["Black"]}
    if major == "1":
        for mid, expected in QF_MATCHES:
            if pair == expected:
                return mid, "quarterfinal"
    elif major == "2":
        for mid, expected in SF_MATCHES:
            if pair == expected:
                return mid, "semifinal"
    elif major == "3":
        mid, expected = THIRD_MATCH
        if pair == expected:
            return mid, "third-place"
        mid, expected = FINAL_MATCH
        if pair == expected:
            return mid, "final"
    raise ValueError(f"Cannot classify round {game.headers['Round']} pair {pair}")


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
    # Cross-check federations against the official FIDE standard list.
    resolved = fide_federations({p.fide_id for p in PLAYERS})
    for player in PLAYERS:
        got = resolved.get(player.fide_id)
        if got and got != player.federation:
            raise ValueError(
                f"Federation disagreement for {player.pgn_name}: "
                f"FIDE list says {got}, builder has {player.federation}"
            )


def compute_match_scores(games: list[chess.pgn.Game]) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for game in games:
        match_id, _ = match_id_of(game)
        white, black = game.headers["White"], game.headers["Black"]
        # Ensure both players appear in every match table.
        scores[match_id].setdefault(white, 0.0)
        scores[match_id].setdefault(black, 0.0)
        result = game.headers["Result"]
        if result == "1-0":
            scores[match_id][white] += 1
        elif result == "0-1":
            scores[match_id][black] += 1
        elif result == "1/2-1/2":
            scores[match_id][white] += 0.5
            scores[match_id][black] += 0.5
        else:
            raise ValueError(f"Unsupported result {result!r}")
    return {mid: dict(table) for mid, table in scores.items()}


def audit_matches(match_scores: dict[str, dict[str, float]]) -> None:
    if set(match_scores) != set(EXPECTED_MATCH_SCORES):
        raise ValueError(f"Match id set {set(match_scores)} != expected {set(EXPECTED_MATCH_SCORES)}")
    for match_id, expected in EXPECTED_MATCH_SCORES.items():
        got = match_scores[match_id]
        if got != expected:
            raise ValueError(f"Match {match_id} scores {got} != published bracket {expected}")


def games_of_match(games: list[chess.pgn.Game], match_id: str) -> list[chess.pgn.Game]:
    """Games in the order they were played inside one match."""
    out = [g for g in games if match_id_of(g)[0] == match_id]
    def keyfn(g: chess.pgn.Game) -> tuple[int, int, int]:
        parts = g.headers["Round"].split(".")
        # (major, sub-match, game_number) -- sub-match handles final's 3.1/3.2.
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0, int(parts[2]) if len(parts) > 2 else 0)
    return sorted(out, key=keyfn)


def game_id_of(game: chess.pgn.Game) -> str:
    """Stable per-game id. The chess.com PGN's Board attribute is not a
    reliable disambiguator (four parallel QF matches all reuse board 1 from
    round 1.1.2 onwards; the two final matches on 15 Aug even mix board 2/3
    labels), so the id is built from the match slug plus the round's
    sub-match and game-number components."""
    mid, _ = match_id_of(game)
    _, sub, game_no = game.headers["Round"].split(".", 2)
    return f"g-{mid.lower().replace('-', '')}-{sub}-{game_no}"


def add_person_name(parent: ET.Element, player: Player) -> None:
    name = child(parent, "name", display=player.display)
    child(name, "family", player.family)
    child(name, "given", player.given)


def build() -> dict[str, object]:
    games = load_pgn(SOURCE_PGN)
    if len(games) != 40:
        raise ValueError(f"Expected 40 games, got {len(games)}")
    audit_players(games)

    # First-observed live rapid rating per player, in file order. Every game
    # in the source is played at 10+0 (rapid), so only a single scope applies.
    rapid_ratings: dict[str, int] = {}
    for game in games:
        initial, increment = parse_time_control(game.headers["TimeControl"])
        if initial != 600 or increment != 0:
            raise ValueError(
                f"Unexpected time control {game.headers['TimeControl']!r} in round {game.headers['Round']}"
            )
        for color in ("White", "Black"):
            name = game.headers[color]
            if name not in rapid_ratings:
                rapid_ratings[name] = int(game.headers[f"{color}Elo"])

    match_scores = compute_match_scores(games)
    audit_matches(match_scores)

    eco_entries = load_eco(ECO_TABLE)

    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-ewc-2026-playoffs"})
    header = child(root, "header")
    child(header, "name", "2026 Esports World Cup Chess Playoffs")
    event_ref = child(
        header, "eventRef",
        ref="event:20260814-20260815-2026-esports-world-cup-chess-playoffs-riyadh",
        source=SITE_SOURCE,
    )
    child(event_ref, "name", "2026 Esports World Cup Chess Playoffs")
    child(header, "eventType", "knockout")
    child(header, "cadence", "rapid")
    child(header, "federation", "KSA")
    dates = child(header, "dates")
    start = child(dates, "start")
    child(start, "day", y=2026, m=8, d=14, iso="2026-08-14")
    end = child(dates, "end")
    child(end, "day", y=2026, m=8, d=15, iso="2026-08-15")
    place = child(header, "placeRef", ref="place:city:KSA-riyadh", kind="city")
    child(place, "name", "Riyadh")
    child(place, "country", "KSA")
    child(place, "city", "Riyadh")
    child(header, "venue", "Esports World Cup (Chess.com broadcast)")
    organizers = child(header, "organizers")
    organizer = child(organizers, "organizer")
    child(organizer, "name", "Chess.com")
    child(organizer, "role", "organizer")

    participants = child(root, "participants")
    # Emit in placement order, then by pgn_name for stable ordering among the
    # tied quarterfinal losers.
    for player in sorted(PLAYERS, key=lambda p: (PLACEMENT[p.pgn_name], p.pgn_name)):
        participant = child(participants, "participant", id=player.participant_id)
        player_ref = child(participant, "playerRef", ref=f"player:fide:{player.fide_id}", source=SITE_SOURCE)
        add_person_name(player_ref, player)
        child(player_ref, "federation", player.federation)
        child(player_ref, "title", player.title)
        ids = child(player_ref, "ids")
        child(ids, "fideId", player.fide_id)
        child(player_ref, "resolution", method="fide-id", resolver="ctml-ewc-2026-playoffs-builder/1")
        snapshot = child(participant, "ratingSnapshot", system="fide", scope="rapid")
        child(snapshot, "value", rapid_ratings[player.pgn_name])
        as_of = child(snapshot, "asOf")
        child(as_of, "month", y=2026, m=8, raw="first observed live rapid rating")
        child(snapshot, "publishedForEvent", "false")
        placement = PLACEMENT[player.pgn_name]
        child(participant, "placement", placement)
        note = placement_note(player.pgn_name, match_scores)
        child(participant, "notes", note)

    games_element = child(root, "games")
    source_uri = SOURCE_PGN.as_uri()
    clocked_plies = 0
    eco_count = 0
    termination_count = 0
    # Emit games in canonical order: round.subround.game_no, then board.
    for game in sorted(games, key=lambda g: tuple(int(x) for x in g.headers["Round"].split(".")) + (int(g.headers["Board"]),)):
        headers = game.headers
        initial, increment = parse_time_control(headers["TimeControl"])
        nodes = list(game.mainline())

        white = PLAYER_BY_PGN[headers["White"]]
        black = PLAYER_BY_PGN[headers["Black"]]
        game_element = child(
            games_element, "game",
            id=game_id_of(game),
            round=headers["Round"], board=headers["Board"],
            white=white.participant_id, black=black.participant_id,
            result=headers["Result"],
        )
        eco = classify_eco(tuple(m.uci() for m in game.mainline_moves()), eco_entries)
        if eco:
            child(game_element, "eco", eco)
            eco_count += 1
        child(game_element, "start", standard="true")
        time_control = child(game_element, "timeControl", cadence="rapid")
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

        # Match/stage grouping is carried structurally by the bracket below;
        # only the cadence segment stays as a tag (every game is rapid here).
        tags = child(game_element, "tags")
        child(tags, "tag", "segment:rapid")

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
    # Leg firstScore/secondScore are the match points a game contributed to
    # the winner/loser side, so a tie's legs sum to its match points.
    bracket = child(root, "bracket", kind="single-elimination")
    for stage_name, stage_order, match_ids in STAGE_PLAN:
        stage_el = child(bracket, "stage", name=stage_name, order=stage_order)
        for match_id in match_ids:
            table = match_scores[match_id]
            winner_name = max(table, key=table.get)
            loser_name = min(table, key=table.get)
            winner, loser = PLAYER_BY_PGN[winner_name], PLAYER_BY_PGN[loser_name]
            tie = child(stage_el, "tie", winner=winner.participant_id)
            child(tie, "side", competitor=winner.participant_id, score=fmt(table[winner_name]), outcome="win")
            child(tie, "side", competitor=loser.participant_id, score=fmt(table[loser_name]), outcome="loss")
            first_sum = second_sum = 0.0
            for leg_no, game in enumerate(games_of_match(games, match_id), start=1):
                result = game.headers["Result"]
                white, black = game.headers["White"], game.headers["Black"]
                if result == "1/2-1/2":
                    first_pts = second_pts = 0.5
                elif result == "1-0":
                    first_pts = 1 if white == winner_name else 0
                    second_pts = 1 if white == loser_name else 0
                else:  # 0-1
                    first_pts = 1 if black == winner_name else 0
                    second_pts = 1 if black == loser_name else 0
                first_sum += first_pts
                second_sum += second_pts
                child(tie, "leg", number=leg_no, firstScore=fmt(first_pts), secondScore=fmt(second_pts),
                      game=game_id_of(game))
            if first_sum != table[winner_name] or second_sum != table[loser_name]:
                raise ValueError(f"{match_id}: legs sum {first_sum}-{second_sum} != match points "
                                 f"{table[winner_name]}-{table[loser_name]}")

    notes = (
        "2026 Esports World Cup Chess Playoffs, an eight-player single-elimination knockout played "
        "14-15 August 2026 as the finale of the wider EWC 2026 chess programme (League Qualifiers, "
        "Play-In and Group Stage on 4-13 August, all preceding the playoffs, are NOT in this file "
        "because no PGN was captured for them). Every game is rapid 10+0 on chess.com. "
        "Match format: race-to-2.5 match points -- a match ends as soon as one player secures the "
        "tie -- with 1 point for a win, 0.5 for a draw. Bracket: four quarterfinals on 14 Aug "
        "(Carlsen-Nihal Sarin, Firouzja-Niemann, Nakamura-Erigaisi, Abdusattorov-Lazavik); two "
        "semifinals on 14 Aug (Carlsen-Firouzja, Nakamura-Lazavik); a third-place match "
        "(Nakamura-Firouzja) and the final (Carlsen-Lazavik) on 15 Aug. The final is contested as "
        "TWO 4-game sets played back-to-back (rounds 3.1 and 3.2 in the source), each of which the "
        "chess.com bracket shows as 3-1; both are combined into a single 8-leg tie in the 'final' "
        "stage, aggregate 6-2 for Carlsen. "
        f"Match results: QF-1 Carlsen {fmt(match_scores['QF-1']['Carlsen, Magnus'])}"
        f"-{fmt(match_scores['QF-1']['Nihal Sarin'])} Nihal Sarin; "
        f"QF-2 Firouzja {fmt(match_scores['QF-2']['Firouzja, Alireza'])}"
        f"-{fmt(match_scores['QF-2']['Niemann, Hans Moke'])} Niemann; "
        f"QF-3 Nakamura {fmt(match_scores['QF-3']['Nakamura, Hikaru'])}"
        f"-{fmt(match_scores['QF-3']['Erigaisi Arjun'])} Erigaisi; "
        f"QF-4 Lazavik {fmt(match_scores['QF-4']['Lazavik, Denis'])}"
        f"-{fmt(match_scores['QF-4']['Abdusattorov, Nodirbek'])} Abdusattorov; "
        f"SF-1 Carlsen {fmt(match_scores['SF-1']['Carlsen, Magnus'])}"
        f"-{fmt(match_scores['SF-1']['Firouzja, Alireza'])} Firouzja; "
        f"SF-2 Lazavik {fmt(match_scores['SF-2']['Lazavik, Denis'])}"
        f"-{fmt(match_scores['SF-2']['Nakamura, Hikaru'])} Nakamura; "
        f"third-place Nakamura {fmt(match_scores['THIRD']['Nakamura, Hikaru'])}"
        f"-{fmt(match_scores['THIRD']['Firouzja, Alireza'])} Firouzja; "
        f"final Carlsen {fmt(match_scores['FINAL']['Carlsen, Magnus'])}"
        f"-{fmt(match_scores['FINAL']['Lazavik, Denis'])} Lazavik. "
        "Final standings: 1 Carlsen, 2 Lazavik, 3 Nakamura, 4 Firouzja, tied 5-8 Abdusattorov, "
        "Erigaisi, Niemann, Nihal Sarin. Player federations were resolved from the in-source FIDE "
        "ids against the official FIDE standard rating list (chess.com does not include federation "
        "in its PGN headers). The knockout is represented with the CTML 2.1 bracket model "
        "(stages -> ties -> legs, one leg per game) and participant placement. Each game also "
        "carries a 'segment:rapid' tag. Every played game, move and per-move clock is stored "
        "losslessly. No engine evaluations were present in the source, so no eval elements were "
        "invented."
    )
    child(root, "notes", notes)

    source = child(root, "source", kind="chess.com-pgn")
    child(source, "uri", source_uri)
    child(source, "note",
          f"40 games; SHA-256 {sha256(SOURCE_PGN)}. Sole source: chess.com broadcast PGN with per-move %clk clocks. "
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


def placement_note(name: str, match_scores: dict[str, dict[str, float]]) -> str:
    """One-line summary of the player's bracket path."""
    def fmt(score: float) -> str:
        return str(int(score)) if float(score).is_integer() else str(score)

    if name == "Carlsen, Magnus":
        return (
            "Final placement: 1st. Won QF-1 over Nihal Sarin "
            f"{fmt(match_scores['QF-1']['Carlsen, Magnus'])}-{fmt(match_scores['QF-1']['Nihal Sarin'])}, "
            f"SF-1 over Firouzja {fmt(match_scores['SF-1']['Carlsen, Magnus'])}-{fmt(match_scores['SF-1']['Firouzja, Alireza'])}, "
            f"and the two-set final over Lazavik 6-2 in aggregate (3-1, 3-1). Tournament champion."
        )
    if name == "Lazavik, Denis":
        return (
            "Final placement: 2nd. Won QF-4 over Abdusattorov "
            f"{fmt(match_scores['QF-4']['Lazavik, Denis'])}-{fmt(match_scores['QF-4']['Abdusattorov, Nodirbek'])} "
            f"and SF-2 over Nakamura {fmt(match_scores['SF-2']['Lazavik, Denis'])}-{fmt(match_scores['SF-2']['Nakamura, Hikaru'])}, "
            "then lost the two-set final to Carlsen 2-6 in aggregate (1-3, 1-3)."
        )
    if name == "Nakamura, Hikaru":
        return (
            "Final placement: 3rd. Won QF-3 over Erigaisi "
            f"{fmt(match_scores['QF-3']['Nakamura, Hikaru'])}-{fmt(match_scores['QF-3']['Erigaisi Arjun'])}, "
            f"lost SF-2 to Lazavik {fmt(match_scores['SF-2']['Nakamura, Hikaru'])}-{fmt(match_scores['SF-2']['Lazavik, Denis'])}, "
            f"then won the third-place match over Firouzja {fmt(match_scores['THIRD']['Nakamura, Hikaru'])}-{fmt(match_scores['THIRD']['Firouzja, Alireza'])}."
        )
    if name == "Firouzja, Alireza":
        return (
            "Final placement: 4th. Won QF-2 over Niemann "
            f"{fmt(match_scores['QF-2']['Firouzja, Alireza'])}-{fmt(match_scores['QF-2']['Niemann, Hans Moke'])}, "
            f"lost SF-1 to Carlsen {fmt(match_scores['SF-1']['Firouzja, Alireza'])}-{fmt(match_scores['SF-1']['Carlsen, Magnus'])}, "
            f"then lost the third-place match to Nakamura {fmt(match_scores['THIRD']['Firouzja, Alireza'])}-{fmt(match_scores['THIRD']['Nakamura, Hikaru'])}."
        )
    qf_losers = {
        "Nihal Sarin":          ("QF-1", "Carlsen, Magnus"),
        "Niemann, Hans Moke":   ("QF-2", "Firouzja, Alireza"),
        "Erigaisi Arjun":       ("QF-3", "Nakamura, Hikaru"),
        "Abdusattorov, Nodirbek": ("QF-4", "Lazavik, Denis"),
    }
    mid, victor = qf_losers[name]
    return (
        f"Final placement: tied 5th-8th. Lost {mid} to {victor} "
        f"{fmt(match_scores[mid][name])}-{fmt(match_scores[mid][victor])}."
    )


if __name__ == "__main__":
    for key, value in build().items():
        print(f"{key}={value}")
