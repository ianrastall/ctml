from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import chess.pgn

from ctml_build import (child, fide_federations, fide_rating_list, fingerprints,
                        game_termination, load_pgn, q, sha256, slug)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = Path(r"D:\elysium\sources\twic")
INFO = SOURCE_DIR / "Tech Mahindra GCL 2026 Info.md"
MAIN_PGN = SOURCE_DIR / "tech-mahindra-global-chess-league-2026-main-event.pgn"
TWIC_PGN = SOURCE_DIR / "globalchessl26.pgn"
LICHESS_PGN = SOURCE_DIR / "lichess_broadcast_tech-mahindra-global-chess-league-2026-finals_uacsaXZb_2026.09.13.pgn"
OUTPUT = ROOT / "tours" / "20260903-20260913_tech-mahindra-global-chess-league.ctml"
RESOLVER = "ctml-gcl-2026-builder/1"

RELATED_QUALIFIERS = [
    ("tech-mahindra-global-chess-league-2026-gcl-open-1-prodigy.pgn", 1006, 12),
    ("tech-mahindra-global-chess-league-2026-gcl-titled-swiss-prodigy.pgn", 225, 0),
    ("tech-mahindra-global-chess-league-2026-gcl-players-swiss.pgn", 35, 0),
    ("tech-mahindra-global-chess-league-2026-gcl-open-1-men.pgn", 640, 16),
]

TEAM_COACHES = {
    "Alpine APL Pipers": "Pravin Thipsay",
    "Ganges Grandmasters": "Baskaran Adhiban",
    "FYERS American Gambits": "Srinath Narayanan",
    "CheQ Mumba Masters": "Pentala Harikrishna",
    "PBG Alaskan Knights": "Swayams Mishra",
    "Triveni Continental Kings": "Loek van Wely",
}

# Rank, match points, GCL game points, final placement. The first three values
# are the published preliminary table; placement incorporates the two final and
# two third-place match legs on 13 September.
EXPECTED_TEAMS = {
    "Ganges Grandmasters": (1, 18, 95, 1),
    "Alpine APL Pipers": (2, 18, 88, 2),
    "FYERS American Gambits": (3, 15, 86, 3),
    "CheQ Mumba Masters": (4, 15, 85, 4),
    "Triveni Continental Kings": (5, 12, 81, 5),
    "PBG Alaskan Knights": (6, 12, 80, 6),
}


@dataclass
class Player:
    fide_id: str
    display: str
    title: str
    rating: int
    team: str
    board: int

    @property
    def participant_id(self) -> str:
        return f"p-fide-{self.fide_id}"


def add_name(parent: ET.Element, display: str) -> None:
    name = child(parent, "name", display=display)
    if "," in display:
        family, given = (part.strip() for part in display.split(",", 1))
        child(name, "family", family)
        if given:
            child(name, "given", given)
    else:
        child(name, "unstructured", display)


def gcl_points(result: str, side: str) -> int:
    if result == "1/2-1/2":
        return 1
    if result == "1-0":
        return 3 if side == "white" else 0
    if result == "0-1":
        return 4 if side == "black" else 0
    raise ValueError(f"Unsupported GCL result {result!r}")


def metadata_key(game: chess.pgn.Game) -> tuple[str, str]:
    h = game.headers
    return h["WhiteFideId"], h["BlackFideId"]


def opening_text(headers: chess.pgn.Headers) -> str | None:
    opening = headers.get("Opening")
    variation = headers.get("Variation")
    if opening and variation:
        return f"{opening} — {variation}"
    return opening or variation


def build() -> dict[str, object]:
    games = load_pgn(MAIN_PGN)
    twic_games = load_pgn(TWIC_PGN)
    lichess_games = load_pgn(LICHESS_PGN)
    if len(games) != 204 or len(twic_games) != 180 or len(lichess_games) != 24:
        raise ValueError(
            f"Unexpected source counts: main={len(games)}, TWIC={len(twic_games)}, "
            f"Lichess={len(lichess_games)}"
        )

    by_round: dict[int, list[chess.pgn.Game]] = defaultdict(list)
    players: dict[str, Player] = {}
    board_counts: dict[str, Counter[int]] = defaultdict(Counter)
    player_team: dict[str, str] = {}
    total_plies = 0
    for game in games:
        h = game.headers
        rnd, board = int(h["Round"]), int(h["Board"])
        by_round[rnd].append(game)
        if h["TimeControl"] != "40/1200+0:0+2" or h["Variant"] != "Standard":
            raise ValueError(f"Unexpected control/variant in match {rnd}, board {board}")
        nodes = list(game.mainline())
        if any(node.clock() is None for node in nodes):
            raise ValueError(f"Missing clock in match {rnd}, board {board}")
        total_plies += len(nodes)
        for color in ("White", "Black"):
            fide_id = h[f"{color}FideId"]
            team = h[f"{color}Team"]
            rating = int(h[f"{color}Elo"])
            candidate = Player(fide_id, h[color], h[f"{color}Title"], rating, team, board)
            if fide_id in players:
                old = players[fide_id]
                if (old.display, old.title, old.rating, old.team) != (
                    candidate.display, candidate.title, candidate.rating, candidate.team
                ):
                    raise ValueError(f"Inconsistent player metadata for FIDE id {fide_id}")
            else:
                players[fide_id] = candidate
            board_counts[fide_id][board] += 1
            player_team.setdefault(fide_id, team)
            if player_team[fide_id] != team:
                raise ValueError(f"Player {fide_id} appears for two teams")

    if set(by_round) != set(range(1, 35)):
        raise ValueError(f"Main-event match numbers are not exactly 1..34: {sorted(by_round)}")
    for rnd, round_games in by_round.items():
        if len(round_games) != 6 or {int(g.headers["Board"]) for g in round_games} != set(range(1, 7)):
            raise ValueError(f"Match {rnd} is not a complete six-board match")
        white_teams = {g.headers["WhiteTeam"] for g in round_games}
        black_teams = {g.headers["BlackTeam"] for g in round_games}
        if len(white_teams) != 1 or len(black_teams) != 1 or white_teams == black_teams:
            raise ValueError(f"Match {rnd} does not have one all-White and one all-Black team")
    if len(players) != 36 or set(player_team.values()) != set(EXPECTED_TEAMS):
        raise ValueError("Expected exactly six teams of six players")
    for fide_id, player in players.items():
        modal = board_counts[fide_id].most_common()
        if len(modal) != 1 or modal[0][0] != player.board:
            raise ValueError(f"Player {player.display} did not remain on one fixed board")

    # TWIC covers the 180 preliminary games and supplies published ECO/opening
    # tags. Date + FIDE-id pair is unique inside that double round robin.
    twic_meta: dict[tuple[str, str], chess.pgn.Game] = {}
    for game in twic_games:
        key = metadata_key(game)
        if key in twic_meta:
            raise ValueError(f"Duplicate TWIC metadata key {key}")
        twic_meta[key] = game
    for game in games:
        if int(game.headers["Round"]) <= 30 and metadata_key(game) not in twic_meta:
            raise ValueError(f"Main preliminary game absent from TWIC: {metadata_key(game)}")

    # The Lichess Finals PGN labels games 31.1..34.6. Its first-game Date tags
    # can say 2026.08.31, so match.board is the reliable join key. Movetext is
    # identical to the main PGN and its [%eval] annotations are retained.
    lichess_meta: dict[tuple[int, int], chess.pgn.Game] = {}
    for game in lichess_games:
        major, board = (int(part) for part in game.headers["Round"].split("."))
        key = major, board
        if key in lichess_meta:
            raise ValueError(f"Duplicate Lichess match/board key {key}")
        lichess_meta[key] = game
    if set(lichess_meta) != {(rnd, board) for rnd in range(31, 35) for board in range(1, 7)}:
        raise ValueError("Lichess Finals source does not contain exactly matches 31..34, boards 1..6")
    for rnd in range(31, 35):
        for game in by_round[rnd]:
            board = int(game.headers["Board"])
            other = lichess_meta[(rnd, board)]
            if (game.headers["WhiteFideId"], game.headers["BlackFideId"], game.headers["Result"]) != (
                other.headers["WhiteFideId"], other.headers["BlackFideId"], other.headers["Result"]
            ):
                raise ValueError(f"Lichess header disagreement in match {rnd}, board {board}")
            if tuple(game.mainline_moves()) != tuple(other.mainline_moves()):
                raise ValueError(f"Lichess movetext disagreement in match {rnd}, board {board}")

    twic_exact = 0
    for game in games:
        if int(game.headers["Round"]) <= 30:
            if tuple(game.mainline_moves()) == tuple(twic_meta[metadata_key(game)].mainline_moves()):
                twic_exact += 1

    # Build all match aggregates. home is the team holding White on all boards.
    matches: list[dict[str, object]] = []
    prelim_mp: Counter[str] = Counter()
    prelim_gp: Counter[str] = Counter()
    prelim_wins: Counter[str] = Counter()
    prelim_losses: Counter[str] = Counter()
    prelim_pairs: Counter[frozenset[str]] = Counter()
    for rnd in range(1, 35):
        round_games = sorted(by_round[rnd], key=lambda g: int(g.headers["Board"]))
        home = round_games[0].headers["WhiteTeam"]
        away = round_games[0].headers["BlackTeam"]
        home_score = sum(gcl_points(g.headers["Result"], "white") for g in round_games)
        away_score = sum(gcl_points(g.headers["Result"], "black") for g in round_games)
        if home_score == away_score:
            home_mp = away_mp = 1
        elif home_score > away_score:
            home_mp, away_mp = 3, 0
        else:
            home_mp, away_mp = 0, 3
        matches.append({
            "round": rnd, "date": round_games[0].headers["Date"], "home": home, "away": away,
            "home_score": home_score, "away_score": away_score,
            "home_mp": home_mp, "away_mp": away_mp, "games": round_games,
        })
        if rnd <= 30:
            prelim_pairs[frozenset((home, away))] += 1
            prelim_gp[home] += home_score
            prelim_gp[away] += away_score
            prelim_mp[home] += home_mp
            prelim_mp[away] += away_mp
            if home_mp > away_mp:
                prelim_wins[home] += 1
                prelim_losses[away] += 1
            elif away_mp > home_mp:
                prelim_wins[away] += 1
                prelim_losses[home] += 1
    if len(prelim_pairs) != 15 or set(prelim_pairs.values()) != {2}:
        raise ValueError("Preliminary stage is not a complete six-team double round robin")
    for team, (rank, mp, gp, placement) in EXPECTED_TEAMS.items():
        if (prelim_mp[team], prelim_gp[team]) != (mp, gp):
            raise ValueError(
                f"Published preliminary standing mismatch for {team}: "
                f"computed {(prelim_mp[team], prelim_gp[team])}, expected {(mp, gp)}"
            )

    expected_postseason = {
        31: frozenset(("CheQ Mumba Masters", "FYERS American Gambits")),
        32: frozenset(("CheQ Mumba Masters", "FYERS American Gambits")),
        33: frozenset(("Ganges Grandmasters", "Alpine APL Pipers")),
        34: frozenset(("Ganges Grandmasters", "Alpine APL Pipers")),
    }
    for match in matches[30:]:
        rnd = int(match["round"])
        if frozenset((str(match["home"]), str(match["away"]))) != expected_postseason[rnd]:
            raise ValueError(f"Unexpected postseason pairing in match {rnd}")

    feds = fide_federations(set(players))
    fide_list = fide_rating_list()
    team_ids = {team: f"t-{slug(team)}" for team in EXPECTED_TEAMS}

    root = ET.Element(q("tournament"), {
        "ctmlVersion": "2.1", "id": "tournament-tech-mahindra-gcl-2026",
    })
    header = child(root, "header")
    child(header, "name", "Tech Mahindra Global Chess League 2026")
    event_ref = child(
        header, "eventRef",
        ref="event:20260903-20260913-tech-mahindra-global-chess-league-2026-bengaluru",
        source=INFO.as_uri(),
    )
    child(event_ref, "name", "Tech Mahindra Global Chess League 2026")
    child(header, "eventType", "team")
    child(header, "eventType", "round-robin")
    child(header, "eventType", "knockout")
    child(header, "cadence", "rapid")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=9, d=3, iso="2026-09-03")
    child(child(dates, "end"), "day", y=2026, m=9, d=13, iso="2026-09-13")
    place = child(
        header, "placeRef", ref="place:city:IND-bengaluru", kind="city", source=INFO.as_uri(),
    )
    child(place, "name", "Bengaluru")
    child(place, "country", "IND")
    child(place, "city", "Bengaluru")
    child(header, "venue", "The Lalit Ashok")
    organizers = child(header, "organizers")
    for name in ("Tech Mahindra", "FIDE"):
        organizer = child(organizers, "organizer")
        child(organizer, "name", name)
        child(organizer, "role", "joint-venture organizer")
    child(header, "rounds", 34)

    participants = child(root, "participants")
    for player in sorted(players.values(), key=lambda p: (EXPECTED_TEAMS[p.team][0], p.board)):
        participant = child(participants, "participant", id=player.participant_id)
        player_ref = child(
            participant, "playerRef", ref=f"player:fide:{player.fide_id}", source=MAIN_PGN.as_uri(),
        )
        add_name(player_ref, player.display)
        if player.fide_id in feds:
            child(player_ref, "federation", feds[player.fide_id])
        child(player_ref, "title", player.title)
        ids = child(player_ref, "ids")
        child(ids, "fideId", player.fide_id)
        child(player_ref, "resolution", method="fide-id", resolver=RESOLVER)
        rating = child(participant, "ratingSnapshot", system="fide", scope="rapid")
        child(rating, "value", player.rating)
        child(child(rating, "asOf"), "month", y=2026, m=9, raw="event rating in source PGN")
        child(rating, "publishedForEvent", "false")

    teams = child(root, "teams")
    for team, (rank, mp, gp, placement) in sorted(EXPECTED_TEAMS.items(), key=lambda item: item[1][0]):
        team_element = child(teams, "team", id=team_ids[team], ref=f"team:{slug(team)}")
        child(team_element, "name", team)
        roster = child(team_element, "roster")
        members = sorted((p for p in players.values() if p.team == team), key=lambda p: p.board)
        if [p.board for p in members] != list(range(1, 7)):
            raise ValueError(f"Roster for {team} does not cover boards 1..6")
        for player in members:
            child(roster, "member", participant=player.participant_id, boardOrder=player.board)
        standing = child(team_element, "standing")
        child(standing, "rank", rank)
        child(standing, "matchPoints", mp)
        child(standing, "gamePoints", gp)
        child(standing, "placement", placement)
        child(
            team_element, "notes",
            f"Head coach: {TEAM_COACHES[team]}. Preliminary record: "
            f"{prelim_wins[team]} wins, {prelim_losses[team]} losses; rank and points are for the "
            "30-match double round robin, while placement is the final overall placing.",
        )

    games_element = child(root, "games")
    game_ids: dict[tuple[int, int], str] = {}
    eval_count = 0
    checkmates = 0
    for match in matches:
        rnd = int(match["round"])
        for game in match["games"]:
            h = game.headers
            board = int(h["Board"])
            gid = f"g-m{rnd}-b{board}"
            game_ids[(rnd, board)] = gid
            white = players[h["WhiteFideId"]]
            black = players[h["BlackFideId"]]
            game_element = child(
                games_element, "game", id=gid, round=rnd, board=board,
                white=white.participant_id, black=black.participant_id,
                whiteTeam=team_ids[h["WhiteTeam"]], blackTeam=team_ids[h["BlackTeam"]],
                result=h["Result"],
            )
            supplemental = twic_meta[metadata_key(game)] if rnd <= 30 else lichess_meta[(rnd, board)]
            eco = supplemental.headers.get("ECO")
            if eco:
                child(game_element, "eco", eco)
            opening = opening_text(supplemental.headers)
            if opening:
                child(game_element, "opening", opening)
            child(game_element, "start", standard="true")
            time_control = child(game_element, "timeControl", cadence="rapid")
            child(time_control, "raw", h["TimeControl"])
            child(time_control, "initialSeconds", 1200)
            child(time_control, "incrementSeconds", 0)
            child(time_control, "note", "No increment through move 40; 2 seconds per move starting at move 41.")

            main_nodes = list(game.mainline())
            eval_nodes = list(lichess_meta[(rnd, board)].mainline()) if rnd > 30 else []
            moves = child(game_element, "moves", notation="uci", plyCount=len(main_nodes), clockInfo="true")
            for index, node in enumerate(main_nodes):
                move = child(
                    moves, "move", ply=node.ply(), value=node.move.uci(),
                    clockSeconds=int(round(node.clock())),
                )
                if eval_nodes:
                    score = eval_nodes[index].eval()
                    if score is not None:
                        relative = score.relative
                        if relative.is_mate():
                            value = relative.mate()
                            if value is None:
                                raise ValueError(f"Missing mate value in match {rnd}, board {board}")
                            child(move, "eval", kind="mate", value=value)
                        else:
                            value = relative.score()
                            if value is None:
                                raise ValueError(f"Missing centipawn value in match {rnd}, board {board}")
                            child(move, "eval", kind="cp", value=value)
                        eval_count += 1
            termination = game_termination(game)
            if termination:
                child(game_element, "termination", termination)
                checkmates += 1
            tags = child(game_element, "tags")
            if rnd <= 30:
                child(tags, "tag", "stage:preliminary")
            elif rnd <= 32:
                child(tags, "tag", "stage:third-place")
            else:
                child(tags, "tag", "stage:final")
            trajectory, final_position = fingerprints(game)
            fps = child(game_element, "fingerprints")
            child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=trajectory)
            child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final_position)

            source = child(game_element, "source", kind="chess.com-pgn")
            child(source, "uri", MAIN_PGN.as_uri())
            child(
                source, "note",
                f"Primary movetext and clocks. PGN Date={h['Date']}; EndDate={h['EndDate']}; "
                f"EndTime={h['EndTime']}; WhiteClock={h['WhiteClock']}; BlackClock={h['BlackClock']}.",
            )
            source = child(game_element, "source", kind="twic-pgn" if rnd <= 30 else "lichess-pgn")
            child(
                source, "uri",
                TWIC_PGN.as_uri() if rnd <= 30 else supplemental.headers.get("GameURL", LICHESS_PGN.as_uri()),
            )
            child(
                source, "note",
                "Published ECO/opening metadata. "
                + ("The main-event PGN is authoritative where movetext differs."
                   if rnd <= 30 else "Lichess evaluations are retained on the corresponding moves."),
            )

    team_matches = child(root, "teamMatches")
    for match in matches:
        rnd = int(match["round"])
        tm = child(
            team_matches, "teamMatch", round=rnd,
            home=team_ids[str(match["home"])], away=team_ids[str(match["away"])],
        )
        child(tm, "homeScore", match["home_score"])
        child(tm, "awayScore", match["away_score"])
        child(tm, "matchPoints", home=match["home_mp"], away=match["away_mp"])
        boards = child(tm, "boards")
        for board in range(1, 7):
            child(boards, "board", number=board, game=game_ids[(rnd, board)])
        child(
            tm, "notes",
            f"Played {match['date']}. home is the all-White team. Scores use GCL game points: "
            "White win 3, Black win 4, draw 1 each; match points are 3/1/0.",
        )

    bracket = child(root, "bracket", kind="other")
    postseason = {
        "third-place": {
            "winner": "FYERS American Gambits", "other": "CheQ Mumba Masters", "rounds": (31, 32),
        },
        "final": {
            "winner": "Ganges Grandmasters", "other": "Alpine APL Pipers", "rounds": (33, 34),
        },
    }
    for stage_name, data in postseason.items():
        winner = str(data["winner"])
        other = str(data["other"])
        rounds = tuple(data["rounds"])
        totals = {winner: 0, other: 0}
        leg_scores: list[tuple[int, int, int]] = []
        for rnd in rounds:
            match = matches[rnd - 1]
            score = {
                str(match["home"]): int(match["home_score"]),
                str(match["away"]): int(match["away_score"]),
            }
            totals[winner] += score[winner]
            totals[other] += score[other]
            leg_scores.append((rnd, score[winner], score[other]))
        stage = child(bracket, "stage", name=stage_name)
        tie = child(stage, "tie", winner=team_ids[winner])
        child(tie, "side", competitor=team_ids[winner], score=totals[winner], outcome="win")
        child(tie, "side", competitor=team_ids[other], score=totals[other], outcome="loss")
        for leg_no, (rnd, winner_score, other_score) in enumerate(leg_scores, start=1):
            leg = child(tie, "leg", number=leg_no, firstScore=winner_score, secondScore=other_score)
            for board in range(1, 7):
                child(leg, "board", number=board, game=game_ids[(rnd, board)])
        child(tie, "notes", "Tie and leg scores are aggregate GCL game points, not ordinary chess points.")
    child(
        bracket, "notes",
        "The top two preliminary teams played a two-leg final; teams ranked third and fourth played a "
        "two-leg third-place tie. Aggregate GCL game points identify the recorded winner in each tie.",
    )

    child(
        root, "notes",
        "Season 4 of the Tech Mahindra Global Chess League at The Lalit Ashok, Bengaluru, 3-13 September "
        "2026; the attached game record runs 5-13 September. Six fixed six-player franchises played a "
        "30-match double round robin (ten matches per team), followed by two legs each for third place and "
        "the championship. All 204 games, 19,852 plies and per-move clocks from the complete main-event PGN "
        "are retained. Ganges Grandmasters finished first, Alpine APL Pipers second, FYERS American Gambits "
        "third, CheQ Mumba Masters fourth, Triveni Continental Kings fifth and PBG Alaskan Knights sixth. "
        f"The TWIC preliminary PGN agrees on every player/result header; {twic_exact}/180 movetexts are exact "
        "matches and the complete clocked main-event PGN is used for the 13 differences. The Lichess Finals "
        "PGN exactly reproduces all 24 postseason movetexts and contributes its 2,305 source evaluations; "
        "its stray 2026.08.31 Date/UTCDate values are not used. The four attached online Swiss PGNs are "
        "separate qualifier events and are not merged into this over-the-board tournament. Two of those "
        "qualifier captures contain parser-damaged 0-0 records, another reason not to imply a combined, "
        "complete event. No missing game, result, player or termination was inferred.",
    )

    source = child(root, "source", kind="chess.com-pgn")
    child(source, "uri", MAIN_PGN.as_uri())
    child(source, "note", f"204 games; primary complete movetext and clock source; SHA-256 {sha256(MAIN_PGN)}.")
    source = child(root, "source", kind="twic-pgn")
    child(source, "uri", TWIC_PGN.as_uri())
    child(source, "note", f"180 preliminary games with ECO/opening tags; SHA-256 {sha256(TWIC_PGN)}.")
    source = child(root, "source", kind="lichess-pgn")
    child(source, "uri", LICHESS_PGN.as_uri())
    child(source, "note", f"24 postseason games with ECO/opening tags and evaluations; SHA-256 {sha256(LICHESS_PGN)}.")
    source = child(root, "source", kind="research-note")
    child(source, "uri", INFO.as_uri())
    child(source, "note", f"Format, venue, roster, scoring and final-standing evidence; SHA-256 {sha256(INFO)}.")
    source = child(root, "source", kind="fide-rating-list")
    child(source, "uri", fide_list.as_uri())
    child(source, "note", f"Player federations resolved by in-source FIDE id; SHA-256 {sha256(fide_list)}.")
    for filename, count, parse_errors in RELATED_QUALIFIERS:
        path = SOURCE_DIR / filename
        source = child(root, "source", kind="related-qualifier-pgn")
        child(source, "uri", path.as_uri())
        detail = f"{count} records"
        if parse_errors:
            detail += f", including {parse_errors} parser-damaged 0-0 records"
        child(
            source, "note",
            f"{detail}; SHA-256 {sha256(path)}. Separate online qualifier, inspected but not merged into "
            "the Bengaluru main event.",
        )

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True, short_empty_elements=True)
    with OUTPUT.open("ab") as stream:
        stream.write(b"\n")

    return {
        "output": str(OUTPUT), "participants": len(players), "teams": len(EXPECTED_TEAMS),
        "games": len(games), "matches": len(matches), "plies": total_plies,
        "lichess_evals": eval_count, "checkmates": checkmates, "twic_exact_movetexts": twic_exact,
        "bytes": OUTPUT.stat().st_size, "sha256": sha256(OUTPUT),
    }


if __name__ == "__main__":
    for key, value in build().items():
        print(f"{key}={value}")
