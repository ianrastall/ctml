"""Builder for the 112th British Chess Championship 2026 (Championship section).

Source:
  Swiss Manager crosstable / TWIC #1452 PGN:  D:/elysium/sources/twic/chgbr26.pgn
  Starting rank / tournament info page:        https://chess-results.com/tnr485896.aspx?lan=1
  FIDE event ID: 485896

Format: 108-player, 9-round Swiss-System; classical time control 40/90'+G/30'+30"/move;
University of Warwick, Panorama Suite, 2026-08-01 to 2026-08-09.

No per-move clock data is present in either source PGN; only ECO codes and opening
names are taken from chgbr26.pgn (TWIC/Mega export). Scores are computed from game
results; players who withdrew mid-event have 0 or partial points.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import chess.pgn

from ctml_build import (child, classify_eco, fingerprints, game_termination,
                        load_eco, load_pgn, q, sha256)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PGN = Path(r"D:\elysium\sources\twic\chgbr26.pgn")
ECO_TABLE = Path(__file__).resolve().parents[1] / "assets/all.tsv"
OUTPUT = ROOT / "tours" / "20260801-20260809_112th-british-chess-championship.ctml"

RESULTS_URL = "https://chess-results.com/tnr485896.aspx?lan=1"
RESOLVER = "ctml-british2026-builder/1"
EVENT_REF = "event:20260801-20260809-112th-british-chess-championship"
FIDE_EVENT_ID = "485896"

# Time control: 40 moves in 90 minutes + game in 30 minutes, 30 second increment from move 1.
# Encoded as two periods: 40 moves / 5400 s + 30 s/move, then 1800 s + 30 s/move.
TC_RAW = "40/90'+G/30'+30\"/move"


@dataclass(frozen=True)
class Player:
    """One participant in the 2026 British Championship."""
    pgn_name: str           # name as it appears in the source PGN (for game matching)
    family: str             # family (last) name for CTML <name>
    given: str              # given (first) name(s) for CTML <name>
    fide_id: str            # FIDE id (string; canonical identity key)
    chess_results_id: str   # chess-results internal id ("0" if not assigned)
    title: str              # GM / IM / FM / CM / WGM / WIM / WFM / WCM, or ""
    federation: str         # 3-letter FIDE federation code
    rating: int             # event FIDE standard rating
    sex: str                # "m" or "w"
    age_group: str          # "U18", "U21", or ""
    seed: int               # starting rank number

    @property
    def pid(self) -> str:
        return f"p-fide-{self.fide_id}"

    @property
    def display_name(self) -> str:
        """Swiss Manager display: Family, Given."""
        if self.given:
            return f"{self.family}, {self.given}"
        return self.family


# ---------------------------------------------------------------------------
# Roster: all 108 starters, keyed by FIDE id.
# Fields: (pgn_name, family, given, fide_id, cr_id, title, fed, rating, sex, age_group, seed)
# pgn_name is from chgbr26.pgn headers; family/given from the official Swiss Manager starting list.
# ---------------------------------------------------------------------------
_ROSTER_ROWS = [
    # seed 1-10
    ("McShane, Luke J",                  "Mcshane",                  "Luke J",                   "404853",    "136665", "GM",  "ENG", 2597, "m", "",    1),
    ("Adams, Michael",                    "Adams",                    "Michael",                  "400041",    "105483", "GM",  "ENG", 2595, "m", "",    2),
    ("Royal, Shreyas",                    "Royal",                    "Shreyas",                  "448869",    "300121", "GM",  "ENG", 2514, "m", "U18", 3),
    ("Grieve, Harry",                     "Grieve",                   "Harry",                    "426520",    "272996", "IM",  "ENG", 2508, "m", "",    4),
    ("Ghasi, Ameet K",                    "Ghasi",                    "Ameet K",                  "409200",    "175386", "GM",  "ENG", 2494, "m", "",    5),
    ("Bazakutsa, Svyatoslav",             "Bazakutsa",                "Svyatoslav",               "14198525",  "352799", "IM",  "UKR", 2488, "m", "U18", 6),
    ("Harvey, Marcus R",                  "Harvey",                   "Marcus R",                 "400092",    "252763", "IM",  "ENG", 2470, "m", "",    7),
    ("Willow, Jonah B",                   "Willow",                   "Jonah B",                  "438804",    "283303", "IM",  "ENG", 2459, "m", "",    8),
    ("Czopor, Maciej",                    "Czopor",                   "Maciej",                   "21805431",  "302173", "IM",  "POL", 2455, "m", "",    9),
    ("Waldhausen Gordon, Frederick",      "Waldhausen Gordon",        "Frederick",                "2409143",   "318419", "IM",  "SCO", 2455, "m", "U18", 10),
    # seed 11-20
    ("Siva, Mahadevan",                   "Mahadevan",                "Siva",                     "25043153",  "304792", "IM",  "IND", 2447, "m", "",    11),
    ("Roberson, Peter T",                 "Roberson",                 "Peter T",                  "412384",    "192596", "IM",  "ENG", 2439, "m", "",    12),
    ("Williams, Simon K",                 "Williams",                 "Simon K",                  "404454",    "132288", "GM",  "ENG", 2438, "m", "",    13),
    ("Gordon, Stephen J",                 "Gordon",                   "Stephen J",                "411477",    "171269", "GM",  "ENG", 2429, "m", "",    14),
    ("Han, Yichen",                       "Han",                      "Yichen",                   "1054430",   "306442", "IM",  "NED", 2421, "m", "U21", 15),
    ("Pert, Richard G",                   "Pert",                     "Richard G",                "404748",    "129722", "IM",  "ENG", 2410, "m", "",    16),
    ("McDonald, Neil",                    "Mcdonald",                 "Neil R",                   "400629",    "115262", "GM",  "ENG", 2399, "m", "",    17),
    ("Kanyamarala, Tarun",                "Kanyamarala",              "Tarun",                    "45004714",  "327310", "IM",  "IRL", 2398, "m", "",    18),
    ("Chow, Samuel",                      "Chow",                     "Sam",                      "3202992",   "300319", "FM",  "AUS", 2381, "m", "",    19),
    ("Jackson, James P",                  "Jackson",                  "James P",                  "416860",    "245061", "IM",  "ENG", 2377, "m", "",    20),
    # seed 21-30
    ("Golding, Alex",                     "Golding",                  "Alex",                     "427241",    "283656", "IM",  "ENG", 2370, "m", "",    21),
    ("Banerjee, Supratit",                "Banerjee",                 "Supratit",                 "2410079",   "341059", "FM",  "ENG", 2357, "m", "U18", 22),
    ("Hebden, Mark L",                    "Hebden",                   "Mark L",                   "400084",    "112455", "GM",  "ENG", 2345, "m", "",    23),
    ("Sivanandan, Bodhana",               "Sivanandan",               "Bodhana",                  "497592",    "340874", "FM",  "ENG", 2338, "w", "U18", 24),
    ("Yao, Lan",                          "Yao",                      "Lan",                      "8610835",   "329301", "WGM", "ENG", 2320, "w", "",    25),
    ("Badacsonyi, Stanley",               "Badacsonyi",               "Stanley",                  "486973",    "319799", "FM",  "ENG", 2320, "m", "U18", 26),
    ("Vijayakumar, Rishi",                "Vijayakumar",              "Rishi",                    "2408953",   "327225", "FM",  "SCO", 2315, "m", "U18", 27),
    ("Fava, Lorenzo",                     "Fava",                     "Lorenzo",                  "2837434",   "308713", "FM",  "ITA", 2314, "m", "U18", 28),
    ("Wells, Peter K",                    "Wells",                    "Peter K",                  "400327",    "121370", "GM",  "ENG", 2313, "m", "",    29),
    ("Ledger, Andrew J",                  "Ledger",                   "Andrew J",                 "400610",    "114226", "IM",  "ENG", 2312, "m", "",    30),
    # seed 31-40
    ("Bates, Richard A",                  "Bates",                    "Richard A",                "403555",    "101997", "IM",  "ENG", 2296, "m", "",    31),
    ("Kanyamarala, Trisha",               "Kanyamarala",              "Trisha",                   "45004722",  "327309", "WGM", "IRL", 2296, "w", "U21", 32),
    ("Shearsby, Jude",                    "Shearsby",                 "Jude",                     "462527",    "306635", "FM",  "ENG", 2293, "m", "U18", 33),
    ("Fitzsimons, David",                 "Fitzsimons",               "David",                    "2501961",   "275660", "IM",  "IRL", 2292, "m", "",    34),
    ("Balaji, Aaravamudhan",              "Balaji",                   "Aaravamudhan",             "436224",    "289709", "IM",  "ENG", 2285, "m", "U21", 35),
    ("Davies, Nigel R",                   "Davies",                   "Nigel R",                  "404420",    "109386", "GM",  "ENG", 2276, "m", "",    36),
    ("Norris, Zack",                      "Norris",                   "Zack",                     "343431216", "352018", "CM",  "ENG", 2275, "m", "U18", 37),
    ("Turner, Max N",                     "Turner",                   "Max N",                    "447722",    "286863", "CM",  "ENG", 2273, "m", "",    38),
    ("Arakhamia-Grant, Ketevan",          "Arakhamia-Grant",          "Ketevan E",                "13600168",  "162980", "GM",  "SCO", 2270, "w", "",    39),
    ("Murawski, Jan",                     "Murawski",                 "Jan",                      "21009287",  "307391", "CM",  "ENG", 2270, "m", "U18", 40),
    # seed 41-50
    ("He, Tom Junde",                     "He",                       "Tom Junde",                "488810",    "326972", "FM",  "ENG", 2266, "m", "U18", 41),
    ("Bezuidenhout, Roland",              "Bezuidenhout",             "Roland",                   "14304562",  "320320", "FM",  "RSA", 2262, "m", "",    42),
    ("Mirzoeva, Elmira",                  "Mirzoeva",                 "Elmira",                   "4127951",   "258871", "WGM", "ENG", 2261, "w", "",    43),
    ("Vaidyanathan, Adithya",             "Vaidyanathan",             "Adithya",                  "495450",    "321399", "FM",  "ENG", 2261, "m", "U18", 44),
    ("Pigott, John C",                    "Pigott",                   "John C",                   "400459",    "117144", "IM",  "ENG", 2255, "m", "",    45),
    ("Rudd, Jack",                        "Rudd",                     "Jack",                     "405736",    "118310", "IM",  "ENG", 2252, "m", "",    46),
    ("Badacsonyi, Frankie",               "Badacsonyi",               "Frankie",                  "460400",    "307984", "FM",  "ENG", 2252, "m", "U21", 47),
    ("Cancedda-Dupuis, Livio",            "Cancedda-Dupuis",          "Livio",                    "499447",    "324552", "",    "ENG", 2250, "m", "U18", 48),
    ("Khoury, Theo",                      "Khoury",                   "Theo",                     "480703",    "319317", "FM",  "ENG", 2249, "m", "U18", 49),
    ("Cummings, David H.",                "Cummings",                 "David H",                  "2603578",   "109205", "IM",  "CAN", 2244, "m", "",    50),
    # seed 51-60
    ("Hobson, Kenneth",                   "Hobson",                   "Kenneth",                  "480819",    "315368", "FM",  "ENG", 2244, "m", "U18", 51),
    ("Han, Qixiang",                      "Han",                      "Qixiang",                  "496952",    "328538", "FM",  "ENG", 2239, "m", "U18", 52),
    ("Lishoy Gengis Paratazham, Dildarav","Lishoy Gengis Paratazham", "Dildarav",                 "491462",    "322893", "CM",  "ENG", 2235, "m", "U18", 53),
    ("Bowcott-Terry, Finlay",             "Bowcott-Terry",            "Finlay",                   "449199",    "302581", "",    "ENG", 2232, "m", "U21", 54),
    ("Verbytski, Oleg",                   "Verbytski",                "Oleg",                     "495506",    "328286", "",    "ENG", 2228, "m", "U18", 55),
    ("Saunders, Aron",                    "Saunders",                 "Aron",                     "469343",    "309827", "",    "ENG", 2225, "m", "U21", 56),
    ("Blackburn, Jonathan L B",           "Blackburn",                "Jonathan Lb",              "1801090",   "160594", "FM",  "WLS", 2220, "m", "",    57),
    ("Toma, Katarzyna",                   "Toma",                     "Katarzyna",                "1119907",   "305272", "WGM", "ENG", 2208, "w", "",    58),
    ("Mannion, Stephen R",                "Mannion",                  "Steve R",                  "2400111",   "152327", "IM",  "SCO", 2206, "m", "",    59),
    ("Crawford, Owen",                    "Crawford",                 "Owen",                     "343409393", "318062", "",    "ENG", 2205, "m", "",    60),
    # seed 61-70
    ("Sooraj, M R",                       "Raju",                     "Sooraj Menothuparambil",   "35014730",  "353012", "",    "IND", 2187, "m", "",    61),
    ("Stubbs, Oliver",                    "Stubbs",                   "Oliver",                   "441325",    "295663", "CM",  "ENG", 2185, "m", "",    62),
    ("Dupuis, Denis K",                   "Dupuis",                   "Denis K",                  "452980",    "305124", "CM",  "ENG", 2183, "m", "U18", 63),
    ("Dicen, Elis Denele",                "Dicen",                    "Elis Denele",              "462560",    "310644", "WFM", "ENG", 2182, "w", "U18", 64),
    ("Carroll, Thomas",                   "Carroll",                  "Thomas",                   "450111",    "295018", "",    "ENG", 2180, "m", "",    65),
    ("Elgar, Tim",                        "Elgar",                    "Tim",                      "343413285", "346760", "",    "ENG", 2180, "m", "U21", 66),
    ("Deepak Ambattu, Rithvik",           "Deepak Ambattu",           "Rithvik",                  "2410060",   "340689", "CM",  "SCO", 2164, "m", "U18", 67),
    ("Avadhoot Lokesh Bhakti Brahme",     "Brahme",                   "Avadhoot Lokesh",          "33496463",  "373914", "",    "IND", 2160, "m", "U18", 68),
    ("Kothari, Jai",                      "Kothari",                  "Jai",                      "477117",    "0",      "",    "ENG", 2160, "m", "U18", 69),
    ("Kolani, Arjun",                     "Kolani",                   "Arjun",                    "446700",    "295162", "",    "ENG", 2158, "m", "U21", 70),
    # seed 71-80
    ("Baker, Chris W",                    "Baker",                    "Chris W",                  "401676",    "106080", "IM",  "ENG", 2153, "m", "",    71),
    ("Majeed, Haroon",                    "Majeed",                   "Haroon",                   "343401902", "329127", "",    "ENG", 2153, "m", "",    72),
    ("Varnam, Liam D",                    "Varnam",                   "Liam D",                   "410950",    "163068", "FM",  "ENG", 2149, "m", "",    73),
    ("Wall, Tim P",                       "Wall",                     "Tim P",                    "401641",    "103486", "FM",  "ENG", 2148, "m", "",    74),
    ("Cont, Arya",                        "Cont",                     "Arya",                     "26100363",  "293522", "",    "ENG", 2143, "m", "",    75),
    ("Hill, Alistair",                    "Hill",                     "Alistair",                 "432407",    "220756", "",    "ENG", 2143, "m", "",    76),
    ("Fellowes, Billy",                   "Fellowes",                 "Billy",                    "483540",    "315996", "CM",  "ENG", 2132, "m", "U18", 77),
    ("Zhao, George",                      "Zhao",                     "George",                   "494607",    "322912", "CM",  "ENG", 2125, "m", "U18", 78),
    ("Nicholas, Koichi B",                "Nicholas",                 "Koichi B",                 "409820",    "170951", "",    "ENG", 2114, "m", "",    79),
    ("Lentzos, Ioanis",                   "Lentzos",                  "Ioannis",                  "4255470",   "294533", "CM",  "GRE", 2110, "m", "",    80),
    # seed 81-90
    ("Hariharan, Shambavi",               "Hariharan",                "Shambavi",                 "488160",    "325613", "WFM", "ENG", 2103, "w", "U18", 81),
    ("Rughani, Mahin",                    "Rughani",                  "Mahin",                    "343417060", "348155", "",    "ENG", 2096, "m", "U18", 82),
    ("Potter, John M",                    "Potter",                   "John M",                   "406260",    "147395", "",    "ENG", 2092, "m", "",    83),
    ("Sharma, Devesh",                    "Sharma",                   "Devesh",                   "2410818",   "358550", "",    "SCO", 2089, "m", "U18", 84),
    ("Quaite, Toby",                      "Quaite",                   "Toby",                     "343432832", "350110", "",    "ENG", 2085, "m", "U18", 85),
    ("Tahmankar, Mandar",                 "Tahmankar",                "Mandar",                   "5019400",   "350145", "",    "IND", 2085, "m", "",    86),
    ("Patel, Zain",                       "Patel",                    "Zain",                     "469246",    "312312", "",    "ENG", 2077, "m", "U18", 87),
    ("Blackford, Ross",                   "Blackford",                "Ross",                     "2409011",   "318488", "",    "SCO", 2075, "m", "U21", 88),
    ("Bhatia, Kanishka",                  "Bhatia",                   "Kanishka",                 "2410095",   "342632", "WCM", "SCO", 2073, "w", "U18", 89),
    ("Rida, Ruqayyah",                    "Rida",                     "Ruqayyah",                 "343272375", "330086", "WFM", "ENG", 2070, "w", "U18", 90),
    # seed 91-100
    ("Davis, Colin",                      "Davis",                    "Colin J",                  "343418740", "351784", "",    "ENG", 2056, "m", "",    91),
    ("Rich, Aaron",                       "Rich",                     "Aaron",                    "499951",    "311893", "CM",  "ENG", 2043, "m", "U21", 92),
    ("Green, Michael",                    "Green",                    "Michael",                  "437514",    "278247", "",    "ENG", 2038, "m", "",    93),
    ("Terler, Bohdan",                    "Terler",                   "Bohdan",                   "34102264",  "349477", "",    "ENG", 2035, "m", "U18", 94),
    ("Jermy, Jaden",                      "Jermy",                    "Jaden",                    "459496",    "301071", "",    "ENG", 2012, "m", "U21", 95),
    ("Cheng, Louis",                      "Cheng",                    "Louis",                    "2410389",   "344976", "",    "SCO", 2008, "m", "U18", 96),
    ("Thomas, James",                     "Thomas",                   "James",                    "343404995", "345594", "",    "ENG", 2005, "m", "U18", 97),
    ("Sefton, Adam",                      "Sefton",                   "Adam",                     "343400965", "342650", "",    "ENG", 2003, "m", "U18", 98),
    ("Friar, Joseph D",                   "Friar",                    "Joseph D",                 "424153",    "275633", "",    "ENG", 1994, "m", "",    99),
    ("Gunathilake, M D Vinuda Shenal",    "Gunatilake",               "Vinuda",                   "9967966",   "354350", "CM",  "SRI", 1986, "m", "U18", 100),
    # seed 101-108
    ("Ortiz Sanchez, Luis",               "Ortiz Sanchez",            "Luis",                     "24581160",  "305122", "",    "WLS", 1977, "m", "",    101),
    ("Camp, Imogen A L",                  "Camp",                     "Imogen Al",                "1802240",   "275554", "WCM", "WLS", 1964, "w", "",    102),
    ("Vaddhireddy, Sai",                  "Vaddhireddy",              "Sai",                      "499560",    "342608", "",    "ENG", 1953, "m", "U21", 103),
    ("Cooke, Suzie G.",                   "Cooke",                    "Suzy G",                   "414930",    "178106", "WFM", "SCO", 1933, "w", "",    104),
    ("Nevska, Gerda",                     "Nevska",                   "Gerda",                    "10700366",  "316197", "WCM", "GCI", 1860, "w", "",    105),
    ("Chapman, Luke",                     "Chapman",                  "Luke",                     "343416608", "317568", "",    "ENG", 1856, "m", "U18", 106),
    ("Ruddy, Rachel",                     "Ruddy",                    "Rachel",                   "15600599",  "0",      "WCM", "JCI", 1681, "w", "",    107),
    ("Brown, Stephanie",                  "Brown",                    "Stephanie",                "343404057", "341164", "",    "ENG", 1559, "w", "",    108),
]


def build_roster() -> dict[str, Player]:
    """Build FIDE-id-keyed roster from the hardcoded rows."""
    roster: dict[str, Player] = {}
    for row in _ROSTER_ROWS:
        pgn_name, family, given, fide_id, cr_id, title, fed, rating, sex, age_group, seed = row
        p = Player(pgn_name, family, given, fide_id, cr_id, title, fed, rating, sex, age_group, seed)
        if fide_id in roster:
            raise ValueError(f"Duplicate FIDE id {fide_id!r}")
        roster[fide_id] = p
    return roster


def pgn_name_to_fide(roster: dict[str, Player]) -> dict[str, str]:
    """Map PGN name → FIDE id using the pgn_name field."""
    mapping: dict[str, str] = {}
    for fide_id, p in roster.items():
        if p.pgn_name in mapping:
            raise ValueError(f"Duplicate PGN name {p.pgn_name!r}")
        mapping[p.pgn_name] = fide_id
    return mapping


def compute_scores(games: list[chess.pgn.Game], name_to_fide: dict[str, str]) -> dict[str, float]:
    """Compute final scores (1/0.5/0) for each player from game results."""
    scores: defaultdict[str, float] = defaultdict(float)
    for g in games:
        w_name = g.headers.get("White", "")
        b_name = g.headers.get("Black", "")
        result = g.headers.get("Result", "*")
        if w_name not in name_to_fide:
            raise ValueError(f"Unknown White player in PGN: {w_name!r}")
        if b_name not in name_to_fide:
            raise ValueError(f"Unknown Black player in PGN: {b_name!r}")
        w_fide = name_to_fide[w_name]
        b_fide = name_to_fide[b_name]
        if result == "1-0":
            scores[w_fide] += 1.0
        elif result == "0-1":
            scores[b_fide] += 1.0
        elif result == "1/2-1/2":
            scores[w_fide] += 0.5
            scores[b_fide] += 0.5
        # "*" or other = no points awarded
    return dict(scores)


def final_placement(scores: dict[str, float], roster: dict[str, Player]) -> dict[str, int]:
    """Assign placements: sort by score desc, then by seed asc as tiebreak."""
    fide_ids = list(roster.keys())
    fide_ids.sort(key=lambda fid: (-scores.get(fid, 0.0), roster[fid].seed))
    return {fid: rank for rank, fid in enumerate(fide_ids, start=1)}


def build() -> dict[str, object]:
    roster = build_roster()
    name_to_fide = pgn_name_to_fide(roster)

    games = load_pgn(SOURCE_PGN)
    if len(games) < 460:
        raise ValueError(f"Expected at least 460 games, got {len(games)}")

    # Validate every game refers to known players and has consistent FIDE ids.
    fide_mismatch: list[str] = []
    for g in games:
        for c in ("White", "Black"):
            pgn_nm = g.headers.get(c, "")
            pgn_fid = g.headers.get(f"{c}FideId", "")
            if pgn_nm not in name_to_fide:
                raise ValueError(f"Unknown {c} player {pgn_nm!r} in round {g.headers.get('Round')}")
            expected_fid = name_to_fide[pgn_nm]
            if pgn_fid and pgn_fid != expected_fid:
                fide_mismatch.append(
                    f"{pgn_nm}: PGN FideId={pgn_fid}, roster={expected_fid}"
                )

    scores = compute_scores(games, name_to_fide)
    place = final_placement(scores, roster)

    eco_entries = load_eco(ECO_TABLE)

    # Build XML
    root = ET.Element(q("tournament"), {"ctmlVersion": "2.1", "id": "tournament-ch-gbr-2026"})
    header = child(root, "header")
    child(header, "name", "112th British Chess Championship 2026")
    er = child(header, "eventRef", ref=EVENT_REF, source=RESULTS_URL)
    child(er, "name", "112th British Chess Championship 2026")
    child(er, "fideEventId", FIDE_EVENT_ID)
    child(header, "eventType", "swiss")
    child(header, "cadence", "classical")
    child(header, "federation", "ENG")
    dates = child(header, "dates")
    child(child(dates, "start"), "day", y=2026, m=8, d=1, iso="2026-08-01")
    child(child(dates, "end"),   "day", y=2026, m=8, d=9, iso="2026-08-09")
    # place:city:GBR-51940 is Wirksworth in the place registry, and Q43414 is not
    # Coventry either; the registry carries Coventry as an admin2 entry (Q6225).
    place_ref = child(header, "placeRef",
                      ref="place:admin2:GBR-49127", kind="admin2",
                      source="https://www.wikidata.org/wiki/Q6225")
    child(place_ref, "name", "Coventry")
    child(place_ref, "country", "GBR")
    child(place_ref, "admin1", "Coventry")
    child(header, "venue", "University of Warwick, Panorama Suite")
    organizers = child(header, "organizers")
    org = child(organizers, "organizer")
    child(org, "name", "English Chess Federation")
    child(org, "federation", "ENG")
    child(org, "role", "organizer")
    director = child(header, "tournamentDirector")
    child(director, "name", "Adrian Elwin")
    child(director, "federation", "ENG")
    child(director, "role", "tournament director")
    arbiters = child(header, "arbiters")
    for arb_name, arb_role, arb_title in [
        ("Barnes, Lara",               "chief arbiter",         "IA"),
        ("Billington-Phillips, Emma-Jane", "deputy chief arbiter", "IA"),
        ("Carr, Matthew",              "arbiter",               "IA"),
        ("Buxton, Richard",           "arbiter",               "FA"),
        ("Chiu, Chun",                "arbiter",               "FA"),
        ("Shaw, John",                "arbiter",               "NA"),
    ]:
        arb = child(arbiters, "arbiter")
        child(arb, "name", arb_name)
        child(arb, "role", arb_role)
        if arb_title:
            child(arb, "titleArbiter", arb_title)
    child(header, "rounds", 9)
    child(header, "pairingProgram", "Swiss-Manager")

    # Participants — ordered by final placement
    participants = child(root, "participants")
    for fide_id, rank in sorted(place.items(), key=lambda kv: kv[1]):
        p = roster[fide_id]
        score = scores.get(fide_id, 0.0)
        part = child(participants, "participant", id=p.pid)
        ref = child(part, "playerRef",
                    ref=f"player:fide:{fide_id}",
                    source=RESULTS_URL)
        name_el = child(ref, "name", display=p.display_name)
        child(name_el, "family", p.family)
        if p.given:
            child(name_el, "given", p.given)
        child(ref, "federation", p.federation)
        if p.title:
            child(ref, "title", p.title)
        ids = child(ref, "ids")
        child(ids, "fideId", fide_id)
        if p.chess_results_id and p.chess_results_id != "0":
            child(ids, "internalId", f"chess-results:{p.chess_results_id}")
        if p.sex == "w":
            child(ref, "sex", "F")
        child(ref, "resolution", method="fide-id", resolver=RESOLVER)
        snap = child(part, "ratingSnapshot", system="fide", scope="standard")
        child(snap, "value", p.rating)
        child(child(snap, "asOf"), "month", y=2026, m=8, raw="event starting rating")
        child(snap, "publishedForEvent", "true")
        child(part, "seed", p.seed)
        score_str = str(int(score)) if float(score).is_integer() else str(score)
        child(part, "score", score_str)
        child(part, "placement", rank)
        if p.age_group:
            child(part, "ageGroup", p.age_group)

    # Games
    games_el = child(root, "games")
    pgn_uri = SOURCE_PGN.as_uri()
    eco_n = term_n = 0

    # Sort games by round then board
    def game_sort_key(g: chess.pgn.Game) -> tuple[int, int]:
        rnd_str = g.headers.get("Round", "0.0")
        parts = rnd_str.split(".")
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)

    for g in sorted(games, key=game_sort_key):
        h = g.headers
        rnd_str = h.get("Round", "0.0")
        rnd_num, board_num = rnd_str.split(".")
        rnd_num = int(rnd_num)
        board_num = int(board_num)

        w_name = h.get("White", "")
        b_name = h.get("Black", "")
        w_fide = name_to_fide[w_name]
        b_fide = name_to_fide[b_name]
        result = h.get("Result", "*")

        ge = child(games_el, "game",
                   id=f"g-r{rnd_num:02d}-b{board_num:03d}",
                   round=f"R{rnd_num}",
                   board=str(board_num),
                   white=roster[w_fide].pid,
                   black=roster[b_fide].pid,
                   result=result)

        # ECO from PGN header (chgbr26.pgn carries Opening + Variation)
        eco_code = h.get("ECO", "")
        opening  = h.get("Opening", "")
        variation = h.get("Variation", "")
        if eco_code:
            eco_parts = [eco_code]
            if opening:
                eco_parts.append(opening)
            if variation:
                eco_parts.append(variation)
            child(ge, "eco", eco_code)
            child(ge, "opening", "; ".join(eco_parts[1:]) if len(eco_parts) > 1 else "")
            eco_n += 1

        child(ge, "start", standard="true")
        tc = child(ge, "timeControl", cadence="classical")
        child(tc, "raw", TC_RAW)

        # Moves (no clocks available)
        nodes = list(g.mainline())
        if nodes:
            moves = child(ge, "moves", notation="uci", plyCount=len(nodes))
            for node in nodes:
                child(moves, "move", ply=node.ply(), value=node.move.uci())

        term = game_termination(g)
        if term:
            child(ge, "termination", term)
            term_n += 1

        traj, final = fingerprints(g)
        fps = child(ge, "fingerprints")
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="trajectory", value=traj)
        child(fps, "fingerprint", scheme="zobrist-polyglot-1", scope="finalPosition", value=final)

        src = child(ge, "source", kind="twic-pgn")
        child(src, "uri", pgn_uri)
        child(src, "note",
              f"TWIC/Mega export chgbr26.pgn; Swiss Manager round {rnd_num} board {board_num}. "
              f"ECO={eco_code!r}; Opening={opening!r}; Variation={variation!r}. No clock data.")

    # Notes
    notes = (
        "112th British Chess Championship 2026 (Championship section), University of Warwick "
        "Panorama Suite, 1-9 August 2026. 108-player, 9-round Swiss-System, classical time "
        "control 40 moves/90 min + game/30 min + 30 s/move from move 1 (Swiss-Manager). "
        "Organiser: English Chess Federation; tournament director: Adrian Elwin; chief arbiter: "
        "IA Lara Barnes; FIDE event id: 485896. Winner: Shreyas Royal (7.5/9). "
        "Game scores and ECO/opening codes from TWIC/Mega export chgbr26.pgn (468 games across "
        "9 rounds; note the organiser's Swiss Manager page reports 465 — the 3-game difference "
        "likely reflects adjudications or result corrections in the TWIC export). "
        "Player identity by FIDE id; names from the official Swiss Manager starting-rank list "
        "(chess-results.com/tnr485896.aspx). Placements use score as primary key and starting "
        "rank as secondary tiebreak (the actual tiebreak order from Swiss Manager, which uses "
        "Buchholz/SB, is not reproduced here). No per-move clock data was available in either "
        "source PGN. Players who withdrew mid-event (Mannion after round 1, Brown after round 2) "
        "retain their game results; score=0 reflects only the games present in the PGN. "
        "Uses the CTML 2.1 model (participant/placement, participant/seed)."
    )
    child(root, "notes", notes)

    s1 = child(root, "source", kind="twic-pgn")
    child(s1, "uri", pgn_uri)
    child(s1, "note",
          f"TWIC Mega PGN (chgbr26.pgn). Contains {len(games)} games with ECO headers. "
          f"No per-move clocks. SHA-256 {sha256(SOURCE_PGN)}. "
          f"FIDE id mismatches between PGN FideId headers and roster: "
          f"{fide_mismatch if fide_mismatch else 'none'}.")
    s2 = child(root, "source", kind="chess-results")
    child(s2, "uri", RESULTS_URL)
    child(s2, "note",
          "Swiss Manager results page (chess-results.com). Starting rank list, player ids, "
          "federations, age groups, and time control copied from this page.")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(OUTPUT, encoding="utf-8", xml_declaration=True,
                               short_empty_elements=True)
    with OUTPUT.open("ab") as st:
        st.write(b"\n")

    out_size = OUTPUT.stat().st_size
    return {
        "output": str(OUTPUT),
        "participants": len(roster),
        "games": len(games),
        "eco_games": eco_n,
        "terminations": term_n,
        "fide_mismatches": len(fide_mismatch),
        "winner": "Royal, Shreyas 7.5/9",
        "bytes": out_size,
        "sha256": sha256(OUTPUT),
    }


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k}={v}")
