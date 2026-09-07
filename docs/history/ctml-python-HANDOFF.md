# CTML Project Handoff

Target location: `D:\dev\proj\ctml` (schemas live in `D:\dev\proj\ctml\xsd`).

## Status (updated 2026-07-31)

All 9 v2 XSD 1.0 modules have been authored and are in `xsd/`: vocab,
names, dates, entities, places, events, analysis (new), game, core,
plus the `ctml.xsd` umbrella. The full set compiles cleanly under
`lxml.etree.XMLSchema` and has been validated against hand-written
minimal sample documents in `samples/` (a tournament plus all three
registries), including negative tests confirming both `xs:unique`
identity constraints (`UniquePlyPerGame`, `UniqueMonthNumPerYear`)
actually reject violations, not just accept valid input. The original
9 v1 files (as supplied by the project owner, previously kept at
`D:\ctml`) are archived verbatim at `reference/v1-xsd/`.

The names/dates split (the one open call from the original design
session) was resolved: split into `ctml-names.xsd` and
`ctml-dates.xsd`, per the project owner's explicit choice.

### Crosstable pipeline (2026-07-31, session 2)

Discovered that a substantial prior incarnation of this project exists at
`D:\ctml` (git repo, "consolidated from an earlier four-repo split"),
including a working crosstable-to-event-registry pipeline this project
picks up from rather than starting the corpus-building phase from zero:

- `docs/crosstable-format.md` and `docs/corpus-policy.md`: the shared
  crosstable JSON contract and the strict-2000-Elo whole-tournament
  admission policy, both copied in verbatim (schema-agnostic, still apply).
- `readers/`: `ctml_source_common.py` (shared helpers, **ported to v2** --
  see below) plus 4 source readers (TWIC, OlimpBase, NWChess/Minev,
  Chess-Results) and `merge_crosstables.py`, all copied in **unchanged**
  (they only emit the crosstable JSON contract, never touch CTML XML, so
  nothing about the v2 schema affects them).
- `scripts/screen_crosstables.py`: ported with only default-path changes
  (schema-agnostic, works purely off crosstable JSON).
- `scripts/seed_event_registry.py`: ported; needed no logic changes beyond
  default paths because it only calls `PartialDate.element()` from
  `ctml_source_common`, and that's where the real v2 adaptation lives.
- `crosstables/crosstables.json` (159MB, 48,198 records: TWIC 46,472,
  OlimpBase 864, NWChess/Minev 651, Chess-Results 211) and
  `build/crosstables-strict2000.json.orig` (the original screened output)
  were moved into this project by the project owner mid-session.
- `reference/v1-rust-tools/`: a full Rust cargo workspace (9 crates,
  ~17,000 lines: `pgn2ctml`, `twic2ctml`, `edo2ctml`, `chessmetrics2ctml`,
  `ctml2sqlite`, `pgn-dedupe-merge`, `geosite`, plus a `ctml` TUI) also
  existed at `D:\ctml\tools`, described as implementing most of this same
  pipeline already, with a `ctml-model` crate claiming byte-compatibility
  with "the original Python pipeline" (implying yet another, earlier,
  fully-Python version predates even that). Explicit decision: archived as
  reference only; v2 continues in pure Python, not reusing or maintaining
  the Rust workspace. Revisit only if a deliberate reason comes up.

**The only real v2 adaptation needed** was `PartialDate.element()` in
`readers/ctml_source_common.py`: v1 emitted
`<ctml:start y="…" precision="…" iso="…"/>`; v2 emits
`<ctml:start><ctml:day y="…" m="…" d="…" iso="…"/></ctml:start>` (matching
the `ctml-dates.xsd` choice-of-precision redesign from session 1). This one
change was sufficient for the entire pipeline: `seed_event_registry.py`
and `crosstable_to_ctml()` both call `.element()` without knowing its
internal shape.

**End-to-end verification against real data** (not just the small
hand-written samples from session 1):
1. `python scripts/screen_crosstables.py` -> 25,219 of 48,198 admitted
   (matches the original D:\ctml run exactly).
2. `python scripts/seed_event_registry.py` -> 10,433 occurrences, 10,069
   series, 4,274 cross-source merges (also matches exactly).
3. The resulting `registry/events.xml` (10,433 occurrences) validates
   cleanly against `xsd/ctml.xsd` via `lxml.etree.XMLSchema` -- a much
   larger and more structurally diverse real-world check than the
   session-1 sample documents.

**Known, deferred, non-blocking bug found during this work**: 430 of the
players across the full dataset (out of hundreds of thousands of rows, ~115
distinct TWIC source HTML files) have a mangled name containing a literal
U+FFFD, e.g. "Novoborsky \ufffdK" instead of "Novoborsky ŠK". Root-caused
(not just guessed): the raw TWIC HTML contains numeric HTML character
references like `&#352;` (Š), and Python's `html.parser.HTMLParser` with
`convert_charrefs=True` (used in `SimpleTableParser` in
`ctml_source_common.py`) mishandles that specific entity-decoding path in
some cases, producing U+FFFD instead of the correct character. This does
**not** affect event-registry seeding (which never touches player names)
-- it will matter once the player registry is built from these same
readers. Left unfixed pending a session with room to test a fix against
all 115 affected files; don't re-diagnose this from scratch, the cause is
already known.

**Also found and fixed this session**: `D:\ctml`'s `registry/` and
`sources/` directories (91 tracked files, ~2.5M lines, including the
previously-seeded v1.3 `registry/events.xml`) had been deleted from disk
without being committed as deleted -- recovered via `git restore` in
`D:\ctml` (verified: `git status` was clean afterward, `events.xml` back
to its committed 10,435-occurrence state). Cause unknown; flagged to the
project owner rather than silently restored.

### Player registry from the project owner's .ssp (2026-07-31, same session)

The project owner redirected the player-registry phase away from
building bottom-up from crosstables: they already have `ratings260703.ssp`
(514MB, dropped at the project root) -- a Scid-derived registry they built
previously, far richer than a plain spelling-correction file. Format
(reverse-engineered + confirmed with the project owner where ambiguous):

```
@PLAYER "., -_*"

<name> #<title-token>[ <FED>][ [<rating>]][ <birthdate>]
  %Bio <source> <id>            (0+: FIDE, Chessmetrics, Edo, Historical)
  %Elo <year>:<v1>,...,<v12>    (0+, monthly ratings, "?" = unknown)
  = <alias name>                (0+)
```

617,358 top-level records, 544,311 distinct display names (68,451 names
have >1 record -- some are genuinely different people sharing a common
name, some are the same person split across unmerged source captures, e.g.
"Aarthie, Ramaswamy" appears once with no FIDE id and 1995-2000 history,
once with FIDE id 5004373 and 2000-2012+ history).

**Decisions made with the project owner, all load-bearing for
`scripts/ssp_to_ctml_players.py`:**
- Identity merge: two records merge into one CTML player **only** on an
  exact shared `%Bio FIDE` id. Same-name records without a shared FIDE id
  stay separate (per corpus-policy.md's own ratchet model -- don't guess
  identity now, let it resolve later). Verified independently that this
  particular .ssp file has **zero** duplicate FIDE ids already (the
  project owner's own prior tooling had already deduplicated at that
  level) -- so the merge code path exists and is tested (via a synthetic
  fixture) but is a no-op on this specific file. Don't be surprised that a
  real run reports "0 merged"; that's correct for this dataset, not a bug.
- The bracket rating (e.g. "[2402]") is the highest value among that
  record's own `%Elo` entries (confirmed by the project owner) -> maps to
  `<ctml:peak><ctml:value>`, with `achieved` derived by finding the first
  year/month equal to that value.
- Title token, split on "+": sub-tokens starting with "W" imply
  `<ctml:sex>F</ctml:sex>` (W/WC/WCM/WF/WFM/WGM/WIM all share that prefix
  convention in this file); sub-tokens matching CTML's `PlayerTitleType`
  enum exactly become `<ctml:title>` entries. `WC` and `WF` specifically
  are dropped rather than mapped -- neither the project owner nor I could
  determine what they denote (distinct from WCM/WFM in this file, not
  abbreviations of them), and the call was to omit rather than guess.
  `xsd/ctml-entities.xsd` was widened (`title` now `minOccurs="0"
  maxOccurs="unbounded"` on both `PlayerRefType` and `PlayerType`) to hold
  combined tokens like "IM+WGM" as two `<title>` elements.
- `%Elo` maps to one `<ctml:ratingTrack system="combined">` per player,
  matching CTML 1.3's "combined" rating design (highest-precedence source
  per month) -- this file already looks like exactly that blended series.
- Birthdates in this file are always year-only precision
  (confirmed against all 520,962 records that have one) -- no month/day
  handling needed.

**Bug found and fixed, in both the new script and the shared
`readers/ctml_source_common.py`:** `person_name_xml()`'s no-comma branch
(space-separated names with no "Family, Given" comma) emitted `<given>`
elements before `<family>`, violating `PersonNameType`'s `xs:sequence`
(family must come first). This bug **predates this session** -- it was
already present in the original `D:\ctml` pipeline -- and slipped through
undetected there because TWIC/OlimpBase names are almost always
comma-form, so the buggy branch was rarely exercised. The .ssp data (lots
of space-separated, no-comma names) hit it immediately. Fixed in both
places; if a from-scratch reimplementation of `person_name_xml` ever
happens, watch for this exact ordering requirement.

**Result**: 617,358 raw records -> 617,358 CTML players (0 FIDE-id merges,
as expected for this file -- see above), sharded by first letter of
display name into `registry/players/players-{A-Z,OTHER}.xml`, matching the
`players-fide/`, `players-edo/` sharding convention from the prior
project. One genuine data defect found and handled: a single record had
`%Bio FIDE 19` (too short to be a real FIDE id per `FideIdType`'s 4-12
digit pattern) -- routed to `<internalId>fide-raw:19</internalId>`
instead of `<fideId>`, so it neither breaks validation nor gets used as a
merge key. All 27 shards validate against `xsd/ctml.xsd`.

Known, deliberately-unaddressed oddities in the raw .ssp data itself (not
converter bugs): some names carry stray lowercase suffix tokens that look
like leftover source annotations, e.g. `"Teschner, Rudolf h"` (from an
`#HM` "Honorary Master" record) and `"Abdi, Zineb Dina wc"` (from a
dropped `#WC` token) -- these get parsed as literal given-name tokens
since nothing in the data distinguishes them from real name parts. Left
as-is; flagging here so it isn't mistaken for a parsing bug later.

### Place registry from dr5hn/countries-states-cities-database (2026-07-31, same session)

The project owner dropped the release assets from the latest release of
[dr5hn/countries-states-cities-database](https://github.com/dr5hn/countries-states-cities-database)
(a well-known, actively-maintained public geo dataset) into
`reference/location-data/` -- recognized by the exact release-asset naming
convention and CSV/JSON column schema, not something that needed asking
about. Two of the ten files were used:

- `json-countries+states+cities.json.gz` -- a clean nested
  country -> state hierarchy (250 countries, 5,308 states). Used for the
  country and admin1 registry entries. Country objects carry an inline
  `translations` dict (language -> localized name) at no extra parsing
  cost, so country-level `altNames` are populated from that; state/city
  `altNames` are not (would need the separate, much larger
  `translations.csv.gz` -- deferred, not essential for a first build).
- `csv-cities.csv.gz` -- the flat city table (152,970 rows), joined back to
  the hierarchy via its `state_id`/`country_id` foreign keys. Used instead
  of the nested JSON's own (sparser) `cities` arrays because it carries
  `wikiDataId`, `population`, and a `type` column the nested JSON's city
  objects lack.

Skipped, deliberately, without needing to ask (all either redundant with
the two files above or out of scope for a chess-tournament place
registry): `csv-postcodes.csv.gz`, `json-postcodes.json.gz` (no postcode
field in `PlaceType`, not relevant to tournament venues),
`csv-translations.csv.gz` (deferred, see above), `geojson-cities.geojson.gz`,
`json-cities.json.gz`, `parquet-*.gz`, `mongodb-world-mongodb-dump.tar.gz`
(alternative formats/subsets of the same two files already used).

**Mapping onto `xsd/ctml-places.xsd`** (implemented in
`scripts/geodata_to_ctml_places.py`):
- Country -> `kind="country"`, `ref="place:country:<iso3>"`.
- State -> `kind="admin1"`, `ref="place:admin1:<iso3166_2>"` (8 of 5,308
  states lack an iso3166_2 code; those fall back to
  `place:admin1:<country iso3>-<state id>`), `parentRef` to its country.
- City-table row -> the `type` column has 35 distinct messy values (city,
  adm1..adm5, section, district, county, regency, prefecture, banner,
  town, village, and a long tail of similar labels). Simplified:
  `type=="adm1"` rows are **skipped outright** (redundant with the states
  already captured from the JSON -- these are duplicate admin1-level
  entries that leaked into the flat city table); `type=="adm2"` ->
  `kind="admin2"` (17,801 rows, common enough to keep as its own kind);
  everything else -> `kind="city"` (`PlaceKindType` has nothing finer than
  admin2, and for Site-string matching purposes a district/county/village/
  etc. is functionally the same kind of leaf place as a city).
  `ref="place:city:<country iso3>-<row id>"` (or `place:admin2:...` for the
  admin2 case, using the dataset's own stable numeric id -- no hashing
  needed, unlike the old `place:raw:<sha1(...)>` scheme in
  `readers/ctml_source_common.py`'s `place_raw_ref()`), `parentRef` to its
  resolved state, or directly to its country if the row has no resolvable
  state.

**One real bug fixed during this build**: the first draft of
`country_xml()` wrote `<ctml:country>` (meaning "which country is this
place located in") on country-kind entries themselves, pointing a country
at itself. Removed -- a country-kind `PlaceType` has no `<country>` child,
only `<iso2>`/`<iso3>`.

**Result**: 250 countries + 5,308 states + 149,515 cities/admin2 (3,455
adm1-typed rows skipped as designed) = 155,073 places, sharded as
`registry/places/places-countries.xml`, `places-states.xml`, and
`places-cities-<ISO3>.xml` (221 country shards -- not all 250 countries
have cities in the dataset). All shards validate against `xsd/ctml.xsd`.
Additionally cross-checked (Python-side, since `parentRef` is a soft
`xs:token` string reference, not an XSD IDREF -- IDREF can't span multiple
documents anyway, which is why the registry design uses plain tokens):
every one of the 149,515 city `parentRef` values resolves to a real
country or state ref. Zero dangling references.

### PGN importer (2026-07-31, same session)

Target corpus: `D:\..Bookstacks\mega-database-2025-filtered.pgn` (1.5GB,
1,881,897 games, spans 1821-2025). Not confirmed with the project owner by
name, but a very confident inference: `docs/corpus-policy.md` explicitly
names "Mega Database" as a canonical OTB source, this is the only Mega-DB-
shaped file on disk, and the filename's "filtered" plausibly explains why
every sampled slice cleared the 2000-Elo admission floor at 100% (it looks
pre-filtered by rating, not raw). Everything else PGN-shaped scattered
around the drive (`.Chess-Nerd`, `.PGN`, `..Download` -- lichess dumps,
Titled Tuesday, engine games) is out of scope per the OTB-only decision
from session 1 and was left alone.

**`scripts/pgn_to_ctml.py`**, plus three new registry-index modules in
`readers/` (`player_registry_index.py`, `event_registry_index.py`,
`place_registry_index.py`, each reusable independently of the importer).
Pipeline: parse with `chess.pgn` -> group games into tournaments -> resolve
participants/event/place against the three registries -> apply the
corpus's rating-floor admission rule -> emit CTML.

**Tournament grouping**: PGN databases interleave games from thousands of
events in no order. Games are grouped by `(normalized Event, normalized
Site)`, then split into separate tournament occurrences wherever
consecutive games (sorted by date) have a gap of more than
`--max-gap-days` (default 21) -- otherwise two different years of an
annual "City Open" merge into one tournament spanning a year. Verified
against a synthetic fixture
(`reference/v1-rust-tools/crates/pgn2ctml/tests/fixtures/multi-event.pgn`)
that specifically exercises this: two real editions of "City Open"
(1998, 1999) correctly split apart; "Alpha Invitational" and "Alpha
Invitational 2001" (same real event, different Event-tag spelling)
correctly stay separate at this layer, since alias-based event resolution
is a different mechanism (see below) -- not a bug, a layering boundary.

**Moves**: stored as `notation="uci"`. `chess.pgn`'s mainline walk (needed
anyway to validate/number the game) produces UCI moves as a free
byproduct, so there's no separate hashing-time walk needed later --
matches what the phase-5 fingerprinting design already assumed.

**Real bug found and fixed**: the first date parser only accepted fully
numeric `YYYY.MM.DD` and returned `None` for anything else. PGN's
`"??"`-wildcard convention for unknown month/day (`"1994.??.??"`) is not
an edge case -- it's extremely common, especially pre-2000 -- and every
game hitting it silently fell into a `1970-01-01` placeholder-date bucket,
corrupting tournament grouping and permanently defeating event resolution
for anything using it. Fixed by switching `Tournament.start`/`end` from
`datetime.date` to `ctml_source_common.PartialDate` throughout (the same
class `scripts/seed_event_registry.py` already uses), preserving true
precision instead of fabricating a day. This also means
`event:<ref>` construction now uses `PartialDate.compact()`, guaranteeing
ref-format consistency with the registry the crosstables pipeline built.

**Player resolution** (`readers/player_registry_index.py`): no FIDE ID or
clean federation tag exists anywhere in this PGN file (confirmed via a
full-file grep, zero matches) -- Mega Database's own `WhiteTeam`/
`BlackTeam` tags are club/national-team affiliations, not
`FederationCodeType`-shaped codes. So resolution is name-only:
normalized-name exact match (unique candidate required; e.g. "Aaron,
Manuel" is genuinely ambiguous in the registry -- two unmerged records
share that exact name -- so it correctly stays unresolved rather than
guessing) with a surname-unique fallback for bare single-token names. No
fuzzy/edit-distance matching -- ambiguous or unmatched names are meant to
surface as registry candidates for later curation. Sample resolution
rates on real slices: ~100% (177/177) on an early-1820s slice (small,
well-documented historical figures), ~40% (3,734/9,277) on a 20,000-game
slice starting from the beginning of the file. Rate is expected to vary a
lot by era and how obscure the players are; not independently alarming.

**Event resolution** (`readers/event_registry_index.py`), three tiers,
cheapest first: (1) exact-ref hit -- the importer's own synthesized
`event:<start.compact()>-<end.compact()>-<slug(name)>` already matches a
registry key for free when spelling and precision agree; (2)
normalized-name + date-overlap, for same-slug-different-precision or
differently-sourced-but-identically-named events; (3) slug-prefix +
date-overlap, added specifically because TWIC's own crosstable-header
convention (which seeded most of this registry) bakes place and date into
the name itself, e.g. `"Dortmund GER (GER), 9-17 vii 1999"`, while
Mega Database's `Event` tag for the same tournament is just `"Dortmund"`
-- tier 2's exact-slug match can never bridge that gap on its own.

**Verified the resolver mechanism directly is correct** (not just
"probably"): manually queried `EventIndex.resolve("Dortmund",
PartialDate(1999,7,9), PartialDate(1999,7,17))` and got a real match
(`event:19990709-19990717-dortmund-ger-ger-9-17-vii-1999`,
`slug-prefix-date-overlap`). But **the match rate against Mega Database in
bulk is low-to-zero on every sample slice tried**, and the root cause is
now understood, not mysterious: Mega Database doesn't use bare
`"Dortmund"` for the modern SuperGM-era tournament at all -- a full-file
survey turned up over 70 distinct Event-tag spellings for that one series
(`"Dortmund SuperGM 27th"`, `"Dortmund Sparkassen GM"`,
`"Dortmund NRW Cup 50th"`, `"Dortmunder Schachtage-09"`, ...), built
around sponsor name and edition ordinal, while the TWIC-seeded registry's
naming is built around place and date. These two conventions barely
overlap under any reasonable slug-based matching, prefix or otherwise.
This is a genuine, structural cross-source identity-resolution problem,
not a code defect -- closing it further would need something like
stripping sponsor names/edition ordinals into a normalized "core series
name" on both sides, or matching on Site + date + roster overlap instead
of event-name text at all. Flagging as real future work, not silently
declaring it solved.

**Admission floor** (`admits()`): every participant must have a known
rating at or above `--min-elo` (default 2000, matching
`scripts/screen_crosstables.py`'s threshold); `--min-elo 0` disables the
check. Whole-tournament gate, matching corpus-policy.md's "admitted or
excluded whole" rule.

**Registry resolution is deliberately read-only** in this script: it
matches against the existing player/event/place registries but never
writes new entries back into them, even on a miss. Appending
newly-discovered event occurrences or player candidates into the
canonical registries (the "ratchet" and the "as overlapping sources are
processed, append into existing files" roadmap items) is real, designed-
for future work, just not bundled into a bulk PGN import run without a
deliberate decision to mutate 617K/10K/155K-entry shared registries as a
side effect of a test invocation.

**Two smaller things fixed along the way**: an unused `ref_prefix`
parameter in `tournament_xml()` (dead code, removed); a debugging
detour where a hand-rolled Python scan for "the first Dortmund game dated
1999" gave a false positive (it never reset its `event` tracking variable
between unrelated records), which is why the "0 matches" result briefly
looked like a resolver bug before direct testing disproved that.

**All sample runs validated against `xsd/ctml.xsd`** (5 tournaments from
the synthetic fixture; 73 tournaments from a 500-game Mega DB slice;
1,808 tournaments from a 20,000-game slice) -- zero schema errors across
every run. The full 1,881,897-game corpus has not been run yet; that's a
real next step, not attempted this session given the scope already
covered.

### Zobrist fingerprinting (2026-08-01, same session)

Built as a standalone, independently-tested primitive first (the project
owner's framing: "the lowest-level of the concerns"), then wired into the
importer's existing move-walk -- in that order, deliberately, so a bug in
the hashing math couldn't hide behind a bug in PGN parsing or vice versa.

**`readers/fingerprint.py`**. Uses `chess.polyglot.zobrist_hash()` --
python-chess's implementation of the standard Polyglot scheme -- not a
hand-rolled one, per the reasoning already on record in this doc (a
from-scratch Zobrist implementation's most common bugs are castling-rights
and en-passant state; reuse a trusted implementation instead of creating a
new place for that same bug). `scheme="zobrist-polyglot-1"` for both
fingerprint scopes, with the value bytes defined precisely (not just "a
hash") since that's the whole point of pinning a scheme token:

- `scope="finalPosition"`: the raw 8-byte big-endian
  `chess.polyglot.zobrist_hash()` of the final position, hex-encoded (16
  hex chars). Deliberately **not** run through a second hash layer --
  a future position-search index wants the raw Zobrist value directly.
- `scope="trajectory"`: SHA-256 (32 bytes, 64 hex chars) over the
  concatenation of the 8-byte Zobrist hash of *every* position in the
  game, in order, **including the starting position at ply 0** (so two
  games with identical moves but different starting FENs still get
  different trajectory fingerprints -- verified by test, not just assumed).
  This is the real identity/dedup key; finalPosition must never be used
  for dedup, exactly as the schema's own documentation already said.

**`FingerprintAccumulator`** is fed the board after every ply during a
move-walk the caller is already doing (to extract UCI moves, in the
importer's case), so fingerprinting adds no second replay pass --
matches what the schema's own design doc predicted back in session 1.
A standalone `compute_fingerprints(uci_moves, start_fen=None)` wrapper
exists too, for tests and any future backfill pass over CTML files that
predate fingerprinting.

**Correctness properties proven with tests before wiring anything in**
(not just asserted): determinism (same input -> same output, always);
different games produce different trajectory hashes; **the critical
property the schema's own documentation specifically warns about** --
transposition (`1.c4 e5 2.Nc3` vs `1.Nc3 e5 2.c4`, same final position,
different move order) produces an **identical** `finalPosition` hash but a
**different** `trajectory` hash, proving the two scopes actually behave
as designed rather than accidentally collapsing into the same thing;
starting-position sensitivity (empty move list, two different starting
FENs, different trajectory hashes -- proves ply-0 is actually included,
not just claimed to be); and value byte-lengths (16 hex chars for
finalPosition, 64 for trajectory).

**Wired into `scripts/pgn_to_ctml.py`**: every emitted `<ctml:game>` now
carries a `<ctml:fingerprints>` block. Tested at increasing scale: the
small synthetic fixture (5 tournaments), then a real 5,000-game Mega
Database slice (1,049 tournaments, 10,000 fingerprint elements -- exactly
5,000 games x 2 scopes, no games silently skipped) -- zero schema errors
throughout. Checked the real corpus slice for any duplicate trajectory
fingerprints (none found in this particular 5,000-game sample -- a
plausible, non-alarming result, not a failed test; determinism is already
proven independently by the unit tests, so a real duplicate showing up
here would have been a nice bonus confirmation, not a requirement).

**Deliberately not done in this pass**: fingerprints are computed at
*import* time now, ahead of the original roadmap's step-5 sequencing
("once all OTB sources have been converted... turn on game-content
hashing"). This is fine, not a contradiction -- the roadmap's sequencing
was about when hashing-based *dedup* logic goes live across the whole
corpus, not about whether newly-imported games are allowed to carry a
fingerprint before that point. Nothing yet *consumes* these fingerprints
for dedup decisions (`pgn_to_ctml.py`'s registry resolution is still
metadata/name-based, as it already was) -- that consumption step, plus a
backfill pass for anything imported before fingerprinting existed, is
still ahead.

### Dedup logic and registry-append logic (2026-08-01, same session)

Built in the order requested: dedup first (a self-contained concern about
the *output corpus*), then registry-append (a separate, riskier concern
about mutating the *shared canonical registries*).

**`readers/corpus_writer.py`** -- `merge_tournament()`. `--out-dir` for
`scripts/pgn_to_ctml.py` is now a **persistent corpus directory**
(convention: `corpus/otb/`), not disposable scratch -- one file per
tournament, dedup-safe on every re-run. Two identity levels:

- **Tournament-level**: one file per event. The obvious approach --
  filename derived from the event ref -- has a real gap for *unresolved*
  events (no registry match): their ref is synthesized fresh from each
  import batch's own observed date span (`event:<start>-<end>-<slug>`), so
  a later, fuller capture of the same real tournament (one more round,
  pushing the end date later) computes a *different* ref and would create
  a duplicate file instead of extending the existing one. Found this by
  actually testing the "broaden coverage" scenario, not by inspection.
  Fixed with `find_matching_file()`: before falling back to a fresh file,
  scan the corpus for an existing tournament whose header name slugs the
  same and whose date range overlaps or is within `--max-gap-days` of the
  incoming range -- the exact same identity heuristic
  `group_tournaments()` already uses *within* one import batch, now also
  applied *across* batches/runs. Registry-resolved events don't have this
  problem (their ref is the stable registry ref regardless of which games
  are in a given batch), so this only matters for the unresolved case --
  which, per the event-resolution findings above, is the common case for
  Mega Database.
- **Game-level, within a tournament**: identity is `(white participant
  ref, black participant ref, round)` -- not the trajectory fingerprint
  alone, since an empty or very short game would fingerprint-collide with
  any other equally short game between different players. The slot is the
  real identity; the fingerprint confirms or distinguishes within it:
  - No existing game in that slot -> append it (new participants get
    fresh local IDs, existing ones are reused).
  - Existing game, fingerprints match (or either side lacks one, e.g. an
    existing record predates fingerprinting) -> same game, already
    recorded; a missing fingerprint gets enriched onto the existing
    record rather than treated as new data.
  - Existing game, fingerprints differ -> genuine **divergence**: two
    sources disagree about what happened. Never silently resolved --
    existing data always wins, the incoming version is dropped from the
    merge, and the conflict is logged (to stderr via a caller-supplied
    `log()` callback) for a human to investigate. Not embedded in the
    CTML document itself (`GameType` has no notes field, deliberately --
    it's scoped to "what was recorded," not curation metadata).
- **Broadening**: if the incoming batch's date range extends past what's
  currently on file, the existing tournament's header `<dates>` gets
  extended to the union -- "including broadening a tournament's
  round/game coverage if an earlier source was incomplete" is explicit
  roadmap language, not an incidental side effect.

Tested in order of increasing complexity, each proven before moving to the
next: **idempotency** (re-running the identical fixture twice -> 0 new
games/participants, identical byte counts, all 5 tournaments correctly
recognized as already present); **append/broadening** (a supplementary
PGN adding a round 4 to "Alpha Invitational" lands in the *same* existing
file, not a new one, and the header's end date extends from 05-03 to
05-04); **divergence** (a conflicting version of an existing round-1 game
-- same white/black/round, different moves and result -- is logged and
the existing `1-0` result is confirmed unchanged in the file afterward,
not silently overwritten by the conflicting `0-1`). Then at real-data
scale: two overlapping 3,000-game Mega Database slices (games 0-3,000 and
2,000-5,000, a genuine 1,000-game overlap) merged into **exactly 5,000
total games** in the resulting 1,046-file corpus -- the precise
mathematical union, no double-counting, zero schema errors, zero
divergences (unsurprising: overlapping slices of the *same* source file
don't actually disagree with themselves).

**`readers/event_registry_writer.py`** -- the splice-in-new-entries logic
was **extracted from `scripts/seed_event_registry.py`** into a shared
module rather than reimplemented, so both the crosstables-sourced seeding
path and the new PGN-sourced path use one tested mechanism instead of two
that could drift apart. Verified the refactor is behavior-preserving: ran
the refactored `seed_event_registry.py` against a fresh registry with the
original crosstables input and got byte-identical output (diffed against
the real `registry/events.xml`) plus the exact original numbers (10,433
occurrences, 10,069 series, 4,274 cross-source merges).

**`scripts/pgn_to_ctml.py --register-new-events`** (off by default, real
reason recorded in the flag's own help text and here): tournaments whose
event didn't match an existing registry occurrence get appended as new
`eventOccurrence` entries via the shared writer. Deliberately **not**
automatic, because of the event-resolution finding earlier in this
document -- TWIC's place+date naming and Mega Database's sponsor+edition
naming barely overlap, so most "unresolved" events from Mega Database are
not actually new tournaments, they're tournaments that already exist in
the registry under a different spelling. Auto-registering them on every
import would pollute the registry with near-duplicate occurrences for
events it already has -- the exact failure mode the registry's whole
identity design exists to prevent. Tested against an isolated *copy* of
the registry (never the real one during testing): 5 unresolved fixture
tournaments correctly added as 5 new occurrences + 4 new series (two
"City Open" editions correctly share one series), schema-valid, and
provably idempotent -- re-running the same import a second time added
zero duplicates, because the newly-registered occurrences immediately
became resolvable via the exact-ref tier on the very next run (a nice
virtuous-cycle property, not something specifically engineered for).

No Python code beyond the crosstable, player-registry, place-registry,
PGN-import, fingerprinting, dedup, and registry-append pipelines above has
been written yet. Running the full 1,881,897-game Mega Database corpus
end-to-end is the obvious next real milestone; a deliberate decision about
whether/when to run `--register-new-events` against the real registry
(not a test copy) is still open, given the pollution risk described
above.

### Notable deviations during authoring (read before extending the schema)

- **PartialDateType is a bigger structural change than "swap assert for
  xs:choice" might suggest.** XSD 1.0 has no conditional-required
  mechanism for *attributes* (`xs:choice` only works over element
  content, not attribute groups), so the old flat attribute bag
  (`y`/`m`/`d`/`precision`/`iso`) became a choice of three child
  elements -- `<year y="…"/>`, `<month y="…" m="…"/>`, or
  `<day y="…" m="…" d="…" iso="…"/>` -- each with its own complex type
  (`YearPrecisionDateType`/`MonthPrecisionDateType`/
  `DayPrecisionDateType`) and its own required-attribute signature. The
  old `@precision` attribute and `DatePrecisionType` enum are gone
  entirely: precision is now implied by which child element is
  present, so there's nothing to drift out of sync. This changes
  instance-document shape versus v1 (e.g. `birthDate` now looks like
  `<birthDate><year y="1990"/></birthDate>`, not
  `<birthDate y="1990" precision="year"/>`) -- anything hand-authoring
  or hand-reading v1-shaped CTML data needs to account for this.
- **PersonNameType's blank-display-name check** (`normalize-space(@display)
  != ''` in v1) was replaced with a `NonBlankStringType` simple-type
  pattern restriction (`.*\S.*`) in `ctml-names.xsd`, not deferred to
  Python. Same strength, native XSD 1.0.
- **YearRatingsType's first v1 assert was dead code even in 1.1**: it
  checked each `month/@num` was in [1,12], which `MonthRatingType`'s
  `@num` restriction already enforced structurally. Dropped outright,
  no XSD 1.0 replacement needed. Its second assert (no duplicate
  `@num` within a year) became the `UniqueMonthNumPerYear` xs:unique
  constraint, attached directly to the local `year` element inside
  `MonthlyRatingsType`.
- **Per-module xs:include is deliberately incomplete**, matching v1's
  own convention: e.g. `ctml-game.xsd` references `ctml:EcoCodeType`
  etc. from `ctml-vocab.xsd` without including it directly, same as
  v1's `ctml-game.xsd` did. Modules resolve fully once assembled by
  `ctml-core.xsd` (which includes vocab first), and are not meant to
  be schema-validated standalone. An IDE that lints one module file in
  isolation will show `src-resolve` errors for these cross-module type
  references -- that's expected, not a regression; check by compiling
  `ctml.xsd` (or `ctml-core.xsd`) as a whole instead.
- **`ctml-game.xsd` includes `ctml-analysis.xsd`**, not the other way
  around, even though the recommended authoring order in this doc
  lists game before analysis. `MoveType` (game.xsd) nests `ctml:eval`
  (analysis.xsd's `EvalType`) and `GameType` nests `ctml:fingerprints`
  (analysis.xsd's `FingerprintSetType`), so the include has to run
  that direction. xs:include order doesn't affect the assembled
  component graph either way -- this is just documented so it isn't
  mistaken for a mistake later.

## Project Goal

A pipeline, not a GUI (for now):

```text
PGNs  --->  CTML files  --->  single PGN
```

Ordered roadmap as stated by the project owner:

1. Stand up a clean CTML system from scratch (not constrained by any
   prior version).
2. Build the registries (player, place, event).
3. Feed OTB (over-the-board) PGN sources into the system, resolving
   against the registries, producing CTML tournament documents.
4. As overlapping/duplicate sources ("doubles") are processed, append
   new data into the relevant existing CTML files instead of creating
   duplicates -- including broadening a tournament's round/game
   coverage if an earlier source was incomplete.
5. Once all OTB sources have been converted into one deduplicated OTB
   corpus, turn on game-content hashing as part of the appending
   process (see Fingerprint design below).
6. Run engine analysis on the games and store evaluations.
7. Construct a single merged PGN file from the CTML corpus -- this
   becomes the project's actual PGN source of truth for OTB chess.

This is explicitly scoped to OTB games only (not online blitz/bullet,
not engine games) for now.

## Decisions Made

### Language and core library

- **Python**, not tied to any GUI at this stage. Top priority stated
  by the project owner is doing this stably and efficiently, not fast
  delivery of a UI.
- **`python-chess`** is load-bearing across three separate phases of
  the roadmap, not just one:
  - `chess.pgn` for PGN parsing (handles SAN disambiguation, NAGs,
    comments -- no hand-rolled parser needed).
  - `chess.polyglot.zobrist_hash()` for game fingerprinting (phase 5)
    -- a standard, already-implemented, widely-used Zobrist scheme.
    Using this instead of a hand-rolled implementation avoids the
    castling-rights/en-passant bugs that are the most common mistake
    in a from-scratch Zobrist implementation.
  - `chess.engine` for UCI engine communication (phase 6, evals).
- **`lxml.etree`** for constructing/writing CTML documents.

### XML schema version: 1.0, not 1.1

Deliberate downgrade from the prior version's `vc:minVersion="1.1"`.
Reasoning, confirmed this session:

- .NET's native `System.Xml.Schema` validator does not support XSD 1.1
  `xs:assert` at all (confirmed via search).
- Python's best 1.1 option, the `xmlschema` package's `XMLSchema11`
  class, does support assertions but has had real assertion-handling
  bugs on record historically -- workable, not flawless.
- XSD 1.0 has zero tooling caveats anywhere, in any language.

**Validator note (updated from earlier in the session):** the earlier
recommendation of `xmlschema.XMLSchema11` was specifically because of
1.1 assertion support. That need is gone now that the target is 1.0.
For structural validation of the new schema, prefer either
`lxml.etree.XMLSchema` (libxml2-backed, fast, already a natural
pairing with using `lxml` for document construction) or
`xmlschema.XMLSchema10` if the richer decode/to-dict data-binding
features of that package are wanted. Don't reach for the 1.1 code path
at all in the new design.

Most of what XSD 1.1 assertions bought the old schema is recoverable
in 1.0 with standard technique, not a straight loss:

- **Conditional required-ness** (e.g. a partial date's precision
  determining which of month/day/iso may be present) -> express as
  an `xs:choice` between distinct complex types instead of one type
  with a conditional assert.
- **Uniqueness constraints** (e.g. no duplicate month number within a
  rating year, no duplicate ply number within a move list) -> native
  1.0 `xs:unique` identity constraints. See the `UniquePlyPerGame`
  example below.
- **True cross-sibling value comparisons** (e.g. start-date <=
  end-date, white attribute != black attribute) genuinely cannot be
  expressed in XSD 1.0 at all. These move to Python-side validation
  code. This is a normal, unremarkable split (schema = grammar,
  application code = semantic business rules), not a gap to work
  around.

### Schema module plan (new)

The prior 9-file module count was not itself the problem -- each
module was already cohesive, which is healthy decomposition. Two
concrete things were wrong and are fixed in the new plan; one is a
judgment call left open.

**Drop:** `ctml-single.xsd`. It was a hand-maintained flattened
duplicate of every other module, admitted in its own annotation to be
non-canonical, and its name is actively misleading (reads as "single
game" schema; it's actually a "single file" bundle of everything --
this confused a careful read this session). If a one-file bundle is
ever needed, generate it from the modular sources at build time. Don't
hand-maintain a second copy.

**Fix:** the old umbrella (`ctml.xsd`) included both `ctml-core.xsd`
and `ctml-events.xsd` separately, but `ctml-core.xsd` already includes
`ctml-events.xsd` itself -- a redundant double-include (not broken,
XSD includes are idempotent by namespace, just untracked). New plan
uses one clean include chain.

**Open call, not decided:** whether to split the old
`ctml-namedate.xsd` (person names + partial dates, bundled together
seemingly because both were "added later" per the old file's own
comment) into `ctml-names.xsd` and `ctml-dates.xsd`. Genuinely the
project owner's call, not a correctness issue either way.

New module list:

1. `ctml-vocab.xsd` -- controlled vocabularies / enums. Carries over
   largely as-is; it was already assertion-free.
2. `ctml-names.xsd` / `ctml-dates.xsd` (or combined -- open call above)
   -- shared primitive types (person names, partial dates, date
   ranges).
3. `ctml-entities.xsd` -- players + player registry. Carry over the
   ref/resolution/alias pattern; rework the date-precision assert into
   the `xs:choice` pattern described above.
4. `ctml-places.xsd` -- places + place registry. Carry over.
5. `ctml-events.xsd` -- event series + occurrence registry. This is
   the dedup backbone: occurrence aliases record the exact
   Event-header spellings different source databases (the old schema
   named `mega`, `twic`, `chesscom` as examples) used for one
   occurrence, so matching an incoming (Event, Site) pair becomes an
   identity lookup instead of fuzzy matching. Keep this design intact.
6. `ctml-game.xsd` -- recorded game facts: result, termination, tags,
   source, and the restructured move list (see below). Scope stays
   "what was actually recorded," not computed data.
7. `ctml-analysis.xsd` (**new module**) -- fingerprint + eval. Kept
   deliberately separate from `ctml-game.xsd` to preserve the
   boundary between "what the source said" and "what our pipeline
   computed."
8. `ctml-core.xsd` -- driver schema: tournament document root, header,
   participants, games-in-tournament assembly. Carry over.
9. `ctml.xsd` -- thin umbrella, single non-redundant include chain.

### Fingerprint design (in `ctml-analysis.xsd`)

Two distinct fingerprint scopes on purpose -- conflating "same final
position" with "same game" was flagged earlier in the session as the
actual bug risk in naive Zobrist-based dedup (many unrelated games,
especially short draws and common endgames, converge on the same final
position). `trajectory` is the real identity/dedup key; `finalPosition`
is a separate, optional feature for future position-search, and must
never be used for dedup decisions.

```xml
<xs:complexType name="FingerprintType">
  <xs:attribute name="scheme" type="xs:token" use="required"/>  <!-- e.g. "zobrist-polyglot-1" -->
  <xs:attribute name="scope" use="required">
    <xs:simpleType>
      <xs:restriction base="xs:token">
        <xs:enumeration value="trajectory"/>     <!-- whole move sequence: THE identity/dedup key -->
        <xs:enumeration value="finalPosition"/>  <!-- endpoint only: position search, never dedup -->
      </xs:restriction>
    </xs:simpleType>
  </xs:attribute>
  <xs:attribute name="value" type="xs:hexBinary" use="required"/>
</xs:complexType>
```

`scheme` is explicit (not assumed fixed forever) so a future change to
the hashing approach can coexist with old fingerprints during a
transition instead of forcing a one-shot migration. Wrap repeatable
fingerprints in a container element, matching the schema's existing
convention for repeatable sets (`tags`, `ids`, `aliases`).

### Move / eval structure (in `ctml-game.xsd` and `ctml-analysis.xsd`)

Resolved fork: **structured per-move elements**, not a sidecar
ply-indexed analysis block. Reasoning: `python-chess`-based hashing
already means the pipeline walks each game move-by-move internally
(building a `Board`, applying moves one at a time to get positions for
Zobrist hashing) -- structured per-move CTML storage mirrors that walk
instead of adding a second one, so the cost of this approach is
smaller than it looked in isolation.

```xml
<xs:complexType name="MoveType">
  <xs:sequence>
    <xs:element name="eval" type="ctml:EvalType" minOccurs="0" maxOccurs="unbounded"/>
  </xs:sequence>
  <xs:attribute name="ply" type="xs:positiveInteger" use="required"/>
  <xs:attribute name="value" type="xs:token" use="required"/>
  <xs:attribute name="clockSeconds" type="xs:nonNegativeInteger" use="optional"/>
</xs:complexType>

<xs:complexType name="MovesType">
  <xs:sequence>
    <xs:element name="move" type="ctml:MoveType" minOccurs="0" maxOccurs="unbounded"/>
  </xs:sequence>
  <xs:attribute name="notation" type="ctml:MoveNotationType" use="required"/>
  <xs:attribute name="plyCount" type="xs:nonNegativeInteger" use="optional"/>
  <xs:attribute name="clockInfo" type="xs:boolean" use="optional"/>
</xs:complexType>

<xs:element name="moves" type="ctml:MovesType">
  <!-- XSD 1.0: uniqueness constraint instead of the assert we can't use -->
  <xs:unique name="UniquePlyPerGame">
    <xs:selector xpath="ctml:move"/>
    <xs:field xpath="@ply"/>
  </xs:unique>
</xs:element>

<xs:complexType name="EvalType">
  <xs:annotation>
    <xs:documentation>
      value is signed, from the perspective of the side to move at this ply --
      matches UCI engine output directly, so no sign-flipping is needed when
      storing raw analysis results.
    </xs:documentation>
  </xs:annotation>
  <xs:attribute name="kind" use="required">
    <xs:simpleType>
      <xs:restriction base="xs:token">
        <xs:enumeration value="cp"/>    <!-- centipawns -->
        <xs:enumeration value="mate"/>  <!-- mate distance, full moves -->
      </xs:restriction>
    </xs:simpleType>
  </xs:attribute>
  <xs:attribute name="value" type="xs:integer" use="required"/>
  <xs:attribute name="engine" type="xs:token" use="optional"/>
  <xs:attribute name="depth" type="xs:nonNegativeInteger" use="optional"/>
</xs:complexType>
```

Design notes baked into the above, stated explicitly so they aren't
silently re-litigated later:

- `ply` is a single sequential counter, not move-number-plus-side --
  side is derivable from parity (odd = White), and storing it
  separately would just be a field that can drift out of sync.
- `value` on `MoveType` holds the move in whatever notation
  `moves/@notation` declares (SAN/UCI/LAN/PGN).
- `eval` uses an explicit `kind` discriminator (`cp` vs `mate`) rather
  than a type union, because both are plain integers and a union can't
  tell them apart without an artificial pattern restriction.
- `eval` is repeatable per move so a later re-analysis adds another
  entry instead of overwriting history -- mirrors how `ratingHistory`
  already treats snapshots as additive elsewhere in the schema.
- `xs:unique` on ply catches duplicate ply numbers only, not gaps or
  out-of-order sequences -- that stays application-level, consistent
  with the rest of the 1.0-vs-app-code split above.
- Known cost: structured per-move XML is meaningfully larger on disk
  than the flat move string it replaces -- a real multiplier at corpus
  scale, not a rounding error. Mitigated by gzip (the structure is
  highly repetitive) and not a scale-breaking problem since files
  aren't held fully in memory at once.
- Hashing runs off the parsed position sequence in `python-chess`
  either way -- independent of how moves end up serialized in CTML.

### Pre-hash safety net

Hashing is sequenced *after* the initial OTB corpus is built (roadmap
step 5), relying on registry-level identity (Event+Site+Round+White+
Black+Result) to catch most duplicates during initial ingestion. That
sequencing is sound -- but before hashing exists, if an incoming game
matches an existing one on metadata, the pipeline should still compare
the actual movetext and record a divergence (using the schema's
existing multi-`source` pattern) rather than silently trust whichever
source arrived first. Cheap to add, closes a real gap in the
pre-hashing phase.

### Registry persistence approach

Registries (players/places/events) are the right size to hold as an
in-memory lookup structure (dict keyed by fide-id / name / alias),
built from the registry XML at the start of a run, used to resolve
incoming names during import, and periodically checkpointed back to
the canonical registry XML files. This is not a scale risk: see Scale
Reference below.

## Superseded / Rejected -- do not re-suggest

- **SQLite as the primary persistent store.** Rejected. CTML XML files
  are the source of truth. SQLite (or a plain in-memory dict) may
  still appear as a transient resolution index during import, never
  as the store of record.
- **Scid-style flat `.ssp` correction-file normalization** (mapping
  old-name -> new-name pairs for Player/Event/Site/Round text).
  Superseded by CTML's registry + ref + resolution audit-trail model,
  which is a strict improvement (machine-readable resolution
  provenance vs. blind text replacement). **Note:** the project owner
  has an existing ~500 MB personal `.ssp`-format name-correction
  corpus from prior work that was never integrated. Worth treating as
  potential seed/bootstrap data for the player registry -- not yet
  designed, flagged as open below.
- **Raw C / Win32 for a GUI.** Considered early for a "small utility,"
  rejected as more implementation cost than warranted specifically
  because of the dark-mode requirement (native Win32 common controls
  need real theming work to look right in dark mode).
- **C# desktop GUI stack** -- WinUI 3 + Windows App SDK, C# 14, .NET
  10, MVVM via `CommunityToolkit.Mvvm`, `Microsoft.Data.Sqlite` -- was
  fully designed as a "PGN Toolbox" GUI wrapper. This is **parked, not
  rejected**: out of scope for the current headless-pipeline phase. If
  a GUI is revisited later, note the underlying engine is now Python,
  so either the GUI wraps the Python pipeline (subprocess/service) or
  the stack decision gets revisited -- not yet decided either way.
- **Sidecar ply-indexed analysis block** for evals (moves stay flat
  text, evals in a parallel structure). Considered, rejected in favor
  of structured per-move elements once `python-chess` was confirmed as
  already walking the game move-by-move for hashing anyway.
- **Hand-maintained flattened single-file schema bundle**
  (`ctml-single.xsd` pattern). Rejected for the rewrite; generate a
  bundle at build time if one is ever needed.

## Reference material included

`reference-v1-xsd/` contains the 9 original schema files as uploaded
this session (`ctml.xsd`, `ctml-core.xsd`, `ctml-vocab.xsd`,
`ctml-namedate.xsd`, `ctml-entities.xsd`, `ctml-places.xsd`,
`ctml-game.xsd`, `ctml-events.xsd`, `ctml-single.xsd`). These are
**prior-art reference only** -- do not place them in the active
`xsd/` folder as-is. They were read in full this session; the design
decisions above already account for what to keep, drop, and change
from them.

## Open / unresolved items

- No Python code written at all yet.
- 500 MB `.ssp` corpus integration plan undecided (see Superseded
  section).
- `EvalType` numeric bounds not finalized -- no explicit range
  constraint on centipawn values yet; consider whether one is worth
  adding.
- Exact registry checkpoint mechanics (single growing XML file vs.
  periodic snapshots, checkpoint frequency) not decided.
- Version control for the project not discussed.
- Target Python version not pinned -- 3.11+ is a reasonable default
  given current tooling, but this was not explicitly agreed.

## Recommended next action

Steps 1-4 below are done (see Status above). Step 5 is next.

1. ~~Move this file and `reference-v1-xsd/` into `D:\dev\proj\ctml`~~ --
   done; reference files at `reference\v1-xsd\`.
2. ~~Resolve the names/dates module-split question~~ -- done, split.
3. ~~Author the new XSD 1.0 modules~~ -- done, all 9 modules in `xsd/`.
4. ~~Validate the new schema set structurally~~ -- done via
   `lxml.etree.XMLSchema` against `samples/` (tournament + all three
   registries), including negative tests for both `xs:unique`
   constraints.
5. **Next:** start the Python import pipeline: PGN parsing via
   `chess.pgn`, entity resolution against the in-memory registry
   index, CTML construction via `lxml`. Pin a target Python version
   (3.11+ suggested, not yet agreed) and set up the project's
   dependency management before writing pipeline code.

## Scale reference

ChessBase Mega Database 2026 (the most complete OTB corpus that
exists, current as of this session): approximately 11.7-11.8 million
games spanning 1475-2025, and approximately 1.4 million distinct
player names. Useful ceiling for capacity assumptions -- this project
is scoped to OTB only, so the realistic corpus size for the "one
source of OTB truth" phase should land within this range, not larger.
This is comfortably within reach of an in-memory registry index and a
one-time-per-game Python hashing pass; neither was ever a real scale
risk once the OTB-only scope was set.
