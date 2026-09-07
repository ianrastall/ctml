//! ctml-clean: the Rust half of the CTML project. This binary is meant to
//! become the fast, industrial-strength counterpart to the existing Python
//! pipeline (`D:\dev\proj\ctml`) — same job (crosstable ingest, player/site/
//! event registries, PGN import, Zobrist fingerprinting, dedup), C-like
//! speed. It is not there yet.
//!
//! `stats` is the first real command: it touches every source of truth this
//! repo carries — the SSP master file, the two XML registries, the ECO
//! table, and the scraped crosstables — end to end, at full size, with
//! timing. It reads and counts; it does not yet convert or write anything.
//! That ordering is deliberate: get honest numbers for what's actually on
//! disk before writing code that transforms it.

mod chess;
mod corpus;
mod crosstable;
mod crosstabledup;
mod diff;
mod eco;
mod eventindex;
mod fingerprint;
mod gameimport;
mod movegen;
mod names;
mod pgn;
mod pgntournament;
mod placeindex;
mod playerindex;
mod polyglot_array;
mod registry;
mod san;
mod ssp;
mod tournament;
mod tournamentgroup;
mod xmlplayers;
mod xmlutil;

use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

fn main() -> ExitCode {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let wants_help = raw.get(1).is_some_and(|a| a == "--help" || a == "-h");

    match raw.first().map(String::as_str) {
        None | Some("-h") | Some("--help") => {
            print_top_level_help();
            return ExitCode::SUCCESS;
        }
        Some("help") => {
            match raw.get(1) {
                Some(name) => print_command_help(name),
                None => print_top_level_help(),
            }
            return ExitCode::SUCCESS;
        }
        Some(cmd) if wants_help => {
            print_command_help(cmd);
            return ExitCode::SUCCESS;
        }
        _ => {}
    }

    let mut args = raw.into_iter();
    let command = args.next();

    match command.as_deref() {
        Some("stats") | None => {
            let assets_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            match run_stats(&assets_dir) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("diff-players") => {
            let assets_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            match run_diff_players(&assets_dir) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("ingest-crosstables") => {
            let assets_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            let out_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("out/tournaments"));
            match run_ingest_crosstables(&assets_dir, &out_dir) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("debug-dedup-pair") => {
            let assets_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
            let a: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(0);
            let b: usize = args.next().and_then(|s| s.parse().ok()).unwrap_or(0);
            let entries = crosstable::load(&data_path(&assets_dir, "crosstables/crosstables.json", "crosstables.json")).unwrap();
            crosstabledup::debug_pair(&entries, a, b);
            ExitCode::SUCCESS
        }
        Some("dedup-crosstables") => {
            let assets_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            let out_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("out/tournaments-deduped"));
            match run_dedup_crosstables(&assets_dir, &out_dir) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("fingerprint-selftest") => {
            let spec_path = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("spec/fingerprint.md"));
            match run_fingerprint_selftest(&spec_path) {
                Ok(true) => ExitCode::SUCCESS,
                Ok(false) => ExitCode::FAILURE,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("perft") => {
            let depth: u32 = args.next().and_then(|s| s.parse().ok()).unwrap_or(4);
            let fen = args.next();
            run_perft(depth, fen.as_deref());
            ExitCode::SUCCESS
        }
        Some("movegen-selftest") => {
            let data_path = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("spec/perft-vectors.tsv"));
            match run_movegen_selftest(&data_path) {
                Ok(true) => ExitCode::SUCCESS,
                Ok(false) => ExitCode::FAILURE,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("pgn-import") => {
            let Some(pgn_path) = args.next().map(PathBuf::from) else {
                eprintln!("usage: ctml-clean pgn-import <file.pgn> [source-kind]");
                return ExitCode::FAILURE;
            };
            let source_kind = args.next().unwrap_or_else(|| "pgn".to_string());
            match run_pgn_import(&pgn_path, &source_kind) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("pgn-selftest") => {
            let pgn_path = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("spec/pgn-test-games.pgn"));
            let expected_path = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("spec/pgn-test-games.expected.tsv"));
            match run_pgn_selftest(&pgn_path, &expected_path) {
                Ok(true) => ExitCode::SUCCESS,
                Ok(false) => ExitCode::FAILURE,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some("pgn-to-corpus") => {
            let Some(pgn_path) = args.next().map(PathBuf::from) else {
                eprintln!(
                    "usage: ctml-clean pgn-to-corpus <file.pgn> <corpus-dir> [assets-dir] [min-elo] [source-kind]"
                );
                return ExitCode::FAILURE;
            };
            let corpus_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("out/corpus"));
            let assets_dir = args
                .next()
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")));
            let min_elo: i64 = args.next().and_then(|s| s.parse().ok()).unwrap_or(0);
            let source_kind = args.next().unwrap_or_else(|| {
                pgn_path.file_name().and_then(|n| n.to_str()).unwrap_or("pgn").to_string()
            });
            match run_pgn_to_corpus(&pgn_path, &corpus_dir, &assets_dir, min_elo, &source_kind) {
                Ok(()) => ExitCode::SUCCESS,
                Err(err) => {
                    eprintln!("error: {err}");
                    ExitCode::FAILURE
                }
            }
        }
        Some(other) => {
            eprintln!("ctml-clean: unknown command '{other}'\n");
            print_top_level_help();
            ExitCode::FAILURE
        }
    }
}

/// `(name, one-line summary)`, grouped by category for
/// [`print_top_level_help`]. The category headers live alongside the
/// list itself so the grouping can't silently drift out of sync with
/// what's actually in `main`'s dispatch match.
const COMMAND_GROUPS: &[(&str, &[(&str, &str)])] = &[
    (
        "Read-only diagnostics (no files written)",
        &[
            ("stats", "Scan every real data source under assets/ and report counts + timing."),
            ("diff-players", "Field-by-field diff: SSP master file vs. the XML player registry."),
            ("pgn-import", "Parse + resolve one PGN file's moves; report pass/fail. Writes nothing."),
            ("perft", "Move-generator node count at a given depth/position."),
        ],
    ),
    (
        "Self-tests (no real input needed — verify the build itself)",
        &[
            ("fingerprint-selftest", "Recompute every vector in spec/fingerprint.md and check it matches."),
            ("movegen-selftest", "Run perft against spec/perft-vectors.tsv and check it matches."),
            ("pgn-selftest", "Check PGN parsing against python-chess-verified expected output."),
        ],
    ),
    (
        "Real pipelines (write files)",
        &[
            ("ingest-crosstables", "Convert assets/crosstables.json into one ctml:tournament file per row."),
            ("dedup-crosstables", "Like ingest-crosstables, but merges duplicate captures across sources first — use this one, not ingest-crosstables, for real output."),
            ("pgn-to-corpus", "Full PGN import: parse, resolve against the registries, group into tournaments, write a dedup-safe corpus."),
        ],
    ),
    (
        "Diagnostics",
        &[("debug-dedup-pair", "Trace why two crosstables.json entries did/didn't cluster in dedup-crosstables.")],
    ),
];

fn print_top_level_help() {
    println!("ctml-clean — Rust tooling for the CTML chess data project (canonical project: D:/dev/proj/ctml).\n");
    println!("USAGE:");
    println!("    ctml-clean <COMMAND> [ARGS...]");
    println!("    ctml-clean help <COMMAND>       show full usage, arguments, and defaults for one command");
    println!("    ctml-clean <COMMAND> --help     same, from the command itself\n");
    println!("COMMANDS:");
    for (group, commands) in COMMAND_GROUPS {
        println!("  {group}:");
        let width = commands.iter().map(|(n, _)| n.len()).max().unwrap_or(0);
        for (name, summary) in *commands {
            println!("    {name:width$}   {summary}");
        }
        println!();
    }
    println!("Run `ctml-clean help <command>` for the exact arguments and defaults each one takes —");
    println!("most take everything after the required ones as optional, in a fixed order, not flags.");
}

fn print_command_help(name: &str) {
    let text = match name {
        "stats" => {
            "stats [assets-dir]

    Scans assets/all.tsv, assets/registries/{events.xml,players/,places/},
    assets/crosstables.json, and the newest assets/*.ssp file, and prints a
    count + timing line for each. Read-only — writes nothing. Good first
    command to run to sanity-check your data is where the rest of the
    commands expect it.

    assets-dir   default: assets"
        }
        "diff-players" => {
            "diff-players [assets-dir]

    Parses the newest assets/*.ssp file and assets/registries/players/ into
    typed records, joins them on FIDE id, and reports a field-by-field
    mismatch count for every field (name, federation, title, birth year,
    rating history, aliases, ...). Read-only. Useful after regenerating the
    player registry from a new .ssp snapshot, to check they still agree.

    assets-dir   default: assets"
        }
        "ingest-crosstables" => {
            "ingest-crosstables [assets-dir] [out-dir]

    Converts every row in assets/crosstables.json into one ctml:tournament
    XML file, independently — no cross-source dedup. If the same real
    tournament was scraped by more than one source (TWIC, OlimpBase,
    nwchess-minev, chess-results), you'll get one file per capture. For
    real use, prefer dedup-crosstables instead, which merges those first.

    assets-dir   default: assets
    out-dir      default: out/tournaments"
        }
        "dedup-crosstables" => {
            "dedup-crosstables [assets-dir] [out-dir]

    Like ingest-crosstables, but first clusters crosstables.json rows that
    likely describe the same real-world tournament across different
    scrapers (matching on event-ref, then name similarity + date + roster
    overlap) and merges each cluster into one file before conversion. This
    is the one to use for real output, not ingest-crosstables.

    assets-dir   default: assets
    out-dir      default: out/tournaments-deduped"
        }
        "debug-dedup-pair" => {
            "debug-dedup-pair <entry-index-a> <entry-index-b>

    Diagnostic for dedup-crosstables: prints the exact inputs
    should_link() saw for two specific rows of assets/crosstables.json
    (0-based index into that JSON array — e.g. the Nth object in the file)
    and whether they were judged to describe the same tournament. Use this
    to check a suspected missed or incorrect merge; always reads
    assets/crosstables.json (no assets-dir argument)."
        }
        "fingerprint-selftest" => {
            "fingerprint-selftest [spec-path]

    Recomputes every test vector in the Zobrist fingerprint spec and
    checks the trajectory/finalPosition hashes match exactly. Exit code
    reflects pass/fail. No real data needed — self-contained.

    spec-path   default: spec/fingerprint.md"
        }
        "perft" => {
            "perft <depth> [fen]

    Prints the legal-move-generator node count (perft) at every depth from
    1 to <depth>, from the standard starting position or from <fen> if
    given. A benchmark/diagnostic for the move generator, not a pipeline
    step — see movegen-selftest for the actual correctness check.

    depth   required, e.g. 5
    fen     default: standard starting position"
        }
        "movegen-selftest" => {
            "movegen-selftest [vectors-path]

    Runs perft for every row in the vectors file and checks the node count
    matches the python-chess-verified expected value. Exit code reflects
    pass/fail. No real data needed.

    vectors-path   default: spec/perft-vectors.tsv"
        }
        "pgn-import" => {
            "pgn-import <file.pgn> [source-kind]

    Parses every game in a PGN file, resolves each SAN move to UCI via the
    move generator, computes both Zobrist fingerprints, and reports
    pass/fail counts plus one example <ctml:game> block. Does NOT resolve
    against the registries, group games into tournaments, or write any
    files — this is a fast first check on a new PGN file before running
    the real pipeline. See pgn-to-corpus for that.

    file.pgn      required
    source-kind   default: \"pgn\" (used as <ctml:source kind=\"...\"> in the printed example)"
        }
        "pgn-selftest" => {
            "pgn-selftest [pgn-path] [expected-path]

    Parses every game in pgn-path and checks the resulting UCI move list
    against expected-path (one line per game, python-chess-verified). Exit
    code reflects pass/fail. No real data needed by default.

    pgn-path        default: spec/pgn-test-games.pgn
    expected-path   default: spec/pgn-test-games.expected.tsv"
        }
        "pgn-to-corpus" => {
            "pgn-to-corpus <file.pgn> [corpus-dir] [assets-dir] [min-elo] [source-kind]

    The real PGN import pipeline: parses file.pgn, resolves every game's
    SAN to UCI, computes fingerprints, resolves White/Black/Event/Site
    against the registries under assets-dir, groups games into tournament
    occurrences (same normalized Event+Site, split on a >21-day gap), and
    writes a dedup-safe corpus into corpus-dir — a rerun merges into
    existing files (matched by resolved event, or by name+overlapping
    dates) instead of duplicating, logging any genuine divergence rather
    than overwriting it. Building the player-registry index alone takes
    ~15-20s; a multi-million-game PGN file can take upwards of 15 minutes
    end to end — this is not a quick command.

    file.pgn      required
    corpus-dir    default: out/corpus
    assets-dir    default: assets
    min-elo       default: 0 (no admission filter). The reference
                  pipeline's own default is 2000 — every participant in a
                  tournament must have a known rating at or above this, or
                  the whole tournament is skipped.
    source-kind   default: file.pgn's own filename"
        }
        _ => {
            eprintln!("ctml-clean: no such command '{name}'\n");
            print_top_level_help();
            return;
        }
    };
    println!("{text}");
}

/// Corpus admission floor (`docs/corpus-policy.md` in the reference
/// pipeline): a tournament is admitted only if every participant has a
/// known rating at or above `min_elo`, or the whole tournament is
/// excluded. `min_elo <= 0` disables the check.
fn admits(t: &tournamentgroup::Tournament, min_elo: i64) -> (bool, String) {
    if min_elo <= 0 {
        return (true, String::new());
    }
    let mut lowest: std::collections::HashMap<&str, Option<i64>> = std::collections::HashMap::new();
    for g in &t.games {
        for (name, elo) in [(g.white.as_str(), g.white_elo), (g.black.as_str(), g.black_elo)] {
            if name.is_empty() {
                continue;
            }
            lowest
                .entry(name)
                .and_modify(|cur| {
                    if let Some(v) = elo {
                        if cur.is_none() || v < cur.unwrap() {
                            *cur = Some(v);
                        }
                    }
                })
                .or_insert(elo);
        }
    }
    let missing = lowest.values().filter(|v| v.is_none()).count();
    if missing > 0 {
        return (false, format!("{missing} of {} participants unrated", lowest.len()));
    }
    let below = lowest.values().filter(|v| v.unwrap() < min_elo).count();
    if below > 0 {
        return (false, format!("{below} of {} participants below {min_elo}", lowest.len()));
    }
    (true, String::new())
}

fn run_pgn_to_corpus(
    pgn_path: &Path,
    corpus_dir: &Path,
    assets_dir: &Path,
    min_elo: i64,
    source_kind: &str,
) -> std::io::Result<()> {
    let players_dir = data_path(assets_dir, "registry/players", "registries/players");
    let events_path = data_path(assets_dir, "registry/events.xml", "registries/events.xml");
    let places_dir = data_path(assets_dir, "registry/places", "registries/places");

    let t = Instant::now();
    println!("building player registry index...");
    let player_index = playerindex::build_index(&players_dir)?;
    println!("  indexed {} players   {:>8.2?}", player_index.player_count, t.elapsed());

    let t = Instant::now();
    println!("building event registry index...");
    let event_index = eventindex::build_index(&events_path)?;
    println!("  indexed {} occurrences   {:>8.2?}", event_index.occurrence_count, t.elapsed());

    let t = Instant::now();
    println!("building place registry index...");
    let place_index = placeindex::build_index(&places_dir)?;
    println!("  indexed {} places   {:>8.2?}\n", place_index.place_count, t.elapsed());

    let text = std::fs::read_to_string(pgn_path)?;
    let t = Instant::now();
    let raw_games = pgn::parse_games(&text);
    println!("parsed {} games from {}   {:>8.2?}", raw_games.len(), pgn_path.display(), t.elapsed());

    let t = Instant::now();
    let mut games = Vec::with_capacity(raw_games.len());
    let mut import_failures = 0u64;
    for g in &raw_games {
        match gameimport::import_game(g) {
            Ok(parsed) => games.push(parsed),
            Err(_) => import_failures += 1,
        }
    }
    println!(
        "imported {} games ({import_failures} failed)   {:>8.2?}",
        games.len(),
        t.elapsed()
    );

    let t = Instant::now();
    let tournaments = tournamentgroup::group_tournaments(games, 21);
    println!("grouped into {} tournaments   {:>8.2?}\n", tournaments.len(), t.elapsed());

    let mut stats = pgntournament::ResolutionStats::default();
    let mut admitted = 0u64;
    let mut rejected = 0u64;
    let mut created = 0u64;
    let mut merged = 0u64;
    let mut total_added_games = 0u64;
    let mut total_added_participants = 0u64;
    let mut total_matched_same = 0u64;
    let mut total_enriched = 0u64;
    let mut total_divergences = 0u64;

    let mut corpus_index = corpus::CorpusIndex::build(corpus_dir)?;
    let t = Instant::now();
    for tn in &tournaments {
        let (ok, _reason) = admits(tn, min_elo);
        if !ok {
            rejected += 1;
            continue;
        }
        admitted += 1;
        let tid = format!("t_{}_{}", crate::xmlutil::slug(&tn.event, "unknown"), tn.start.compact());
        let (xml_str, event_ref, _resolved) = pgntournament::tournament_xml(
            tn,
            &tid,
            Some(&player_index),
            Some(&event_index),
            Some(&place_index),
            &mut stats,
            source_kind,
        );
        let mut log = |msg: String| eprintln!("{msg}");
        let report = corpus::merge_tournament(corpus_dir, &xml_str, &event_ref, 21, &mut corpus_index, &mut log)?;
        if report.status == "created" {
            created += 1;
        } else {
            merged += 1;
            total_added_games += report.added_games as u64;
            total_added_participants += report.added_participants as u64;
            total_matched_same += report.matched_same as u64;
            total_enriched += report.enriched as u64;
            total_divergences += report.divergences as u64;
        }
    }

    println!(
        "admitted {admitted} of {} tournaments (floor {min_elo}): {created} new files, {merged} merged \
         (+{total_added_games} games, +{total_added_participants} participants, {total_matched_same} already \
         present, {total_enriched} enriched, {total_divergences} divergences), {rejected} rejected, wrote to {}   {:>8.2?}",
        tournaments.len(),
        corpus_dir.display(),
        t.elapsed()
    );

    if stats.participants_total > 0 {
        println!(
            "player resolution: {}/{} participant slots resolved to a registry ref",
            stats.participants_resolved, stats.participants_total
        );
    }
    if stats.events_total > 0 {
        println!(
            "event resolution: {}/{} tournaments matched an existing registry occurrence",
            stats.events_resolved, stats.events_total
        );
    }
    if stats.places_total > 0 {
        println!(
            "place resolution: {}/{} sites matched an existing registry place",
            stats.places_resolved, stats.places_total
        );
    }

    Ok(())
}

fn run_pgn_import(pgn_path: &Path, source_kind: &str) -> std::io::Result<()> {
    let text = std::fs::read_to_string(pgn_path)?;
    let t = Instant::now();
    let games = pgn::parse_games(&text);
    println!("parsed {} games from {}   {:>8.2?}\n", games.len(), pgn_path.display(), t.elapsed());

    let mut imported = 0u64;
    let mut failed = 0u64;
    let mut total_plies = 0u64;
    let t = Instant::now();
    let mut first_example: Option<String> = None;

    for g in &games {
        match gameimport::import_game(g) {
            Ok(parsed) => {
                imported += 1;
                total_plies += parsed.uci_moves.len() as u64;
                if first_example.is_none() {
                    first_example =
                        Some(gameimport::game_xml(&parsed, "p0001", "p0002", source_kind, "  "));
                }
            }
            Err(err) => {
                failed += 1;
                eprintln!("  FAILED: {err}");
            }
        }
    }

    println!(
        "imported {imported}/{} games ({failed} failed), {total_plies} total plies   {:>8.2?}",
        games.len(),
        t.elapsed()
    );

    if let Some(example) = first_example {
        println!("\nexample <ctml:game> (participant ids are placeholders — tournament grouping isn't wired up yet):\n{example}");
    }

    Ok(())
}

/// Cross-checks `pgn::parse_games` + `gameimport::import_game` against
/// UCI move lists `python-chess` computed for the same PGN text — see
/// `HANDOFF.md` for how `spec/pgn-test-games.pgn` and its `.expected.tsv`
/// were generated.
fn run_pgn_selftest(pgn_path: &Path, expected_path: &Path) -> std::io::Result<bool> {
    let pgn_text = std::fs::read_to_string(pgn_path)?;
    let expected_text = std::fs::read_to_string(expected_path)?;
    let expected: Vec<&str> = expected_text.lines().filter(|l| !l.trim_start().starts_with('#')).collect();

    let games = pgn::parse_games(&pgn_text);
    if games.len() != expected.len() {
        eprintln!(
            "game count mismatch: parsed {} games from {}, but {} has {} expected lines",
            games.len(),
            pgn_path.display(),
            expected_path.display(),
            expected.len()
        );
        return Ok(false);
    }

    let mut all_ok = true;
    let mut pass = 0u32;
    for (i, (g, want)) in games.iter().zip(expected.iter()).enumerate() {
        let event = g.tags.get("Event").cloned().unwrap_or_default();
        match gameimport::import_game(g) {
            Ok(parsed) => {
                let got = parsed.uci_moves.join(" ");
                if got == *want {
                    pass += 1;
                } else {
                    all_ok = false;
                    println!("FAIL game {i} ({event}):");
                    println!("    got:  {got}");
                    println!("    want: {want}");
                }
            }
            Err(err) => {
                all_ok = false;
                println!("FAIL game {i} ({event}): import error: {err}");
            }
        }
    }

    println!("{pass}/{} games matched python-chess exactly", games.len());
    Ok(all_ok)
}

fn run_perft(depth: u32, fen: Option<&str>) {
    let board = match fen {
        Some(f) => chess::Board::from_fen(f).expect("invalid FEN"),
        None => chess::Board::starting_position(),
    };
    for d in 1..=depth {
        let t = Instant::now();
        let nodes = movegen::perft(&board, d);
        println!("perft({d}) = {nodes:>12}   {:>8.2?}", t.elapsed());
    }
}

/// `spec/perft-vectors.tsv`: `label\tfen\tdepth\texpected_nodes` per line,
/// generated against `python-chess`'s own `board.legal_moves` — see
/// `HANDOFF.md` for how.
fn run_movegen_selftest(data_path: &Path) -> std::io::Result<bool> {
    let text = std::fs::read_to_string(data_path)?;
    let mut all_ok = true;
    let mut count = 0u32;

    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<&str> = line.split('\t').collect();
        let [label, fen, depth_s, expected_s] = fields[..] else {
            eprintln!("skipping malformed line: {line}");
            continue;
        };
        let depth: u32 = depth_s.parse().expect("depth must be an integer");
        let expected: u64 = expected_s.parse().expect("expected node count must be an integer");
        count += 1;

        let Some(board) = chess::Board::from_fen(fen) else {
            println!("FAIL {label}  (unparseable FEN: {fen})");
            all_ok = false;
            continue;
        };
        let t = Instant::now();
        let got = movegen::perft(&board, depth);
        let ok = got == expected;
        all_ok &= ok;
        println!(
            "{} {label}  perft({depth}) = {got} (want {expected})   {:>8.2?}",
            if ok { "PASS" } else { "FAIL" },
            t.elapsed()
        );
    }

    println!("\n{count} cases; {}", if all_ok { "all match." } else { "MISMATCH — see above." });
    Ok(all_ok)
}

/// Parses the test-vector table directly out of `spec/fingerprint.md`
/// (rather than retyping 6 hex strings by hand into this file, which is
/// exactly the kind of transcription error the spec's own opening
/// paragraph warns about) and checks every row.
fn run_fingerprint_selftest(spec_path: &Path) -> std::io::Result<bool> {
    let text = std::fs::read_to_string(spec_path)?;
    let mut cases: Vec<(String, Vec<String>, String, String)> = Vec::new();

    for line in text.lines() {
        let line = line.trim();
        if !line.starts_with('|') || !line.contains('`') {
            continue;
        }
        let cells: Vec<&str> = line.trim_matches('|').split('|').map(str::trim).collect();
        if cells.len() != 4 {
            continue;
        }
        let [label, moves_cell, traj_cell, final_cell] = [cells[0], cells[1], cells[2], cells[3]];
        let Some(trajectory) = traj_cell.strip_prefix('`').and_then(|s| s.strip_suffix('`')) else { continue };
        let Some(final_position) = final_cell.strip_prefix('`').and_then(|s| s.strip_suffix('`')) else { continue };
        // Header separator / non-hex rows won't match this once we also
        // require the trajectory cell to look like hex.
        if !trajectory.bytes().all(|b| b.is_ascii_hexdigit()) || trajectory.len() != 64 {
            continue;
        }
        let moves: Vec<String> = if moves_cell.contains("none") {
            Vec::new()
        } else {
            moves_cell.trim_matches('`').split_whitespace().map(str::to_string).collect()
        };
        cases.push((label.to_string(), moves, trajectory.to_string(), final_position.to_string()));
    }

    if cases.is_empty() {
        eprintln!("no test-vector rows found in {} — table format changed?", spec_path.display());
        return Ok(false);
    }

    println!("found {} test vectors in {}\n", cases.len(), spec_path.display());
    let mut all_ok = true;
    for (label, moves, want_traj, want_final) in &cases {
        match fingerprint::compute(moves, None) {
            Some(fp) => {
                let ok = &fp.trajectory == want_traj && &fp.final_position == want_final;
                all_ok &= ok;
                println!(
                    "{} {label}  moves=[{}]",
                    if ok { "PASS" } else { "FAIL" },
                    moves.join(" ")
                );
                if !ok {
                    println!("    trajectory:     got {}", fp.trajectory);
                    println!("                    want {want_traj}");
                    println!("    finalPosition:  got {}", fp.final_position);
                    println!("                    want {want_final}");
                }
            }
            None => {
                all_ok = false;
                println!("FAIL {label}  (move application failed)");
            }
        }
    }

    println!("\n{}", if all_ok { "all vectors match." } else { "MISMATCH — see above." });
    Ok(all_ok)
}

fn run_ingest_crosstables(assets_dir: &Path, out_dir: &Path) -> std::io::Result<()> {
    let crosstables_path = data_path(assets_dir, "crosstables/crosstables.json", "crosstables.json");
    println!("parsing {} ...", crosstables_path.display());
    let t = Instant::now();
    let entries = crosstable::load(&crosstables_path)?;
    println!("  {} raw entries   {:>8.2?}\n", entries.len(), t.elapsed());

    std::fs::create_dir_all(out_dir)?;

    let mut written = 0u64;
    let mut skipped_no_start = 0u64;
    let mut skipped_other = 0u64;
    let mut seen: std::collections::HashMap<String, u32> = std::collections::HashMap::new();

    let t = Instant::now();
    for (idx, entry) in entries.iter().enumerate() {
        match tournament::crosstable_to_ctml(entry) {
            Some(xml) => {
                let base = tournament::base_filename(entry, idx);
                let count = seen.entry(base.clone()).or_insert(0);
                *count += 1;
                let suffix = if *count > 1 { format!("-{count}") } else { String::new() };
                let truncated: String = base.chars().take(150).collect();
                let path = out_dir.join(format!("{truncated}{suffix}.xml"));
                std::fs::write(path, xml)?;
                written += 1;
            }
            None => {
                let has_event = !entry.event.trim().is_empty();
                let has_named_player = entry.players.iter().any(|p| !p.name.trim().is_empty());
                if has_event && has_named_player {
                    // Only remaining reason `crosstable_to_ctml` returns
                    // `None`: missing or unparseable `start`.
                    skipped_no_start += 1;
                } else {
                    skipped_other += 1;
                }
            }
        }
    }

    println!(
        "wrote {written} tournament files to {}\nskipped {skipped_no_start} (no usable start date), {skipped_other} (no event name / no named players)   {:>8.2?}",
        out_dir.display(),
        t.elapsed()
    );

    Ok(())
}

/// Cross-source dedup: clusters `crosstables.json` rows likely
/// describing the same real-world tournament (per
/// `crosstabledup::dedup`) before conversion, instead of emitting one
/// `ctml:tournament` per raw scrape the way `ingest-crosstables` does.
fn run_dedup_crosstables(assets_dir: &Path, out_dir: &Path) -> std::io::Result<()> {
    let crosstables_path = data_path(assets_dir, "crosstables/crosstables.json", "crosstables.json");
    println!("parsing {} ...", crosstables_path.display());
    let t = Instant::now();
    let entries = crosstable::load(&crosstables_path)?;
    println!("  {} raw entries   {:>8.2?}", entries.len(), t.elapsed());

    let t = Instant::now();
    let result = crosstabledup::dedup(entries);
    let merged_count = result.clusters.iter().filter(|(m, _)| *m).count();
    let passthrough_count = result.clusters.len() - merged_count;
    println!(
        "  {} source documents -> {} clusters ({merged_count} multi-entry merges, {passthrough_count} \
         single-entry passthroughs, {} multi-*source* merges), {} entries dropped (no usable start date)   {:>8.2?}\n",
        result.source_documents,
        result.clusters.len(),
        result.multi_source_clusters,
        result.dated_dropped,
        t.elapsed()
    );

    std::fs::create_dir_all(out_dir)?;
    let mut written = 0u64;
    let mut skipped = 0u64;
    let mut seen: std::collections::HashMap<String, u32> = std::collections::HashMap::new();

    let t = Instant::now();
    for (idx, (_, entry)) in result.clusters.iter().enumerate() {
        match tournament::crosstable_to_ctml(entry) {
            Some(xml) => {
                let base = tournament::base_filename(entry, idx);
                let count = seen.entry(base.clone()).or_insert(0);
                *count += 1;
                let suffix = if *count > 1 { format!("-{count}") } else { String::new() };
                let truncated: String = base.chars().take(150).collect();
                let path = out_dir.join(format!("{truncated}{suffix}.xml"));
                std::fs::write(path, xml)?;
                written += 1;
            }
            None => skipped += 1,
        }
    }

    println!(
        "wrote {written} tournament files to {} ({skipped} clusters skipped — best member had no usable start date)   {:>8.2?}",
        out_dir.display(),
        t.elapsed()
    );

    Ok(())
}

fn run_diff_players(assets_dir: &Path) -> std::io::Result<()> {
    let ssp_path = find_ssp(assets_dir)?;
    let players_dir = data_path(assets_dir, "registry/players", "registries/players");

    println!("parsing {} ...", ssp_path.display());
    let t = Instant::now();
    let ssp_result = ssp::parse_players(&ssp_path)?;
    println!(
        "  {} players with a FIDE id, {} without (not compared — no join key), \
         {} malformed name lines, {} comment lines   {:>8.2?}",
        ssp_result.by_fide_id.len(),
        ssp_result.without_fide_id,
        ssp_result.malformed_name_lines,
        ssp_result.comment_lines,
        t.elapsed()
    );

    println!("\nparsing {} ...", players_dir.display());
    let t = Instant::now();
    let xml_result = xmlplayers::parse_players_dir(&players_dir)?;
    println!(
        "  {} players with a FIDE id, {} without, {} shards   {:>8.2?}",
        xml_result.by_fide_id.len(),
        xml_result.without_fide_id,
        xml_result.shards,
        t.elapsed()
    );

    println!("\ncomparing, joined on FIDE id ...\n");
    let t = Instant::now();
    let report = diff::compare(&ssp_result.by_fide_id, &xml_result.by_fide_id);
    diff::print_report(&report);
    println!("\n(comparison took {:>8.2?})", t.elapsed());

    Ok(())
}

fn run_stats(assets_dir: &Path) -> std::io::Result<()> {
    println!("ctml-clean stats — scanning {}\n", assets_dir.display());

    let eco_path = data_path(assets_dir, "assets/all.tsv", "all.tsv");
    let t = Instant::now();
    let eco_rows = eco::load(&eco_path)?;
    println!(
        "eco table       {:<40} {:>10} rows                 {:>8.2?}",
        eco_path.display(),
        eco_rows.len(),
        t.elapsed()
    );

    let events_path = data_path(assets_dir, "registry/events.xml", "registries/events.xml");
    let t = Instant::now();
    let events = registry::scan_events(&events_path)?;
    println!(
        "event registry   {:<40} {:>10} eventSeries          {:>8.2?}",
        events_path.display(),
        events.records,
        t.elapsed()
    );

    let players_dir = data_path(assets_dir, "registry/players", "registries/players");
    let t = Instant::now();
    let (players, player_shards) = registry::scan_players_dir(&players_dir)?;
    println!(
        "player registry  {:<40} {:>10} players, {:>7} aliases, {} shards  {:>8.2?}",
        players_dir.display(),
        players.records,
        players.sub_records,
        player_shards,
        t.elapsed()
    );

    let places_dir = data_path(assets_dir, "registry/places", "registries/places");
    let t = Instant::now();
    let (places, place_shards) = registry::scan_places_dir(&places_dir)?;
    println!(
        "place registry   {:<40} {:>10} places, {} shards            {:>8.2?}",
        places_dir.display(),
        places.records,
        place_shards,
        t.elapsed()
    );

    let crosstables_path = data_path(assets_dir, "crosstables/crosstables.json", "crosstables.json");
    let t = Instant::now();
    let crosstables = crosstable::load(&crosstables_path)?;
    let total_player_rows: usize = crosstables.iter().map(|c| c.players.len()).sum();
    println!(
        "crosstables      {:<40} {:>10} tournaments, {:>7} player-rows   {:>8.2?}",
        crosstables_path.display(),
        crosstables.len(),
        total_player_rows,
        t.elapsed()
    );

    let ssp_path = find_ssp(assets_dir)?;
    let t = Instant::now();
    let ssp_stats = ssp::scan(&ssp_path)?;
    println!(
        "ssp master       {:<40} {:>10} player records       {:>8.2?}",
        ssp_path.display(),
        ssp_stats.player_records,
        t.elapsed()
    );
    println!(
        "                 {:>10} lines, {:>10} bytes, {} aliases, {} elo lines, {} FIDE ids",
        ssp_stats.lines,
        ssp_stats.bytes,
        ssp_stats.player_aliases,
        ssp_stats.player_elo_lines,
        ssp_stats.player_fide_ids
    );
    println!(
        "                 event-name rules {}, site-name rules {}, round-name rules {}",
        ssp_stats.event_rules, ssp_stats.site_rules, ssp_stats.round_rules
    );

    println!();
    let diff = players.records as i64 - ssp_stats.player_records as i64;
    if diff == 0 {
        println!(
            "check: XML player registry ({}) matches SSP player records exactly.",
            players.records
        );
    } else {
        println!(
            "check: XML player registry has {} players, SSP master has {} player records \
             ({:+} difference) — expected if the registry was generated from a different \
             .ssp snapshot; investigate if this repo's data is meant to be in sync.",
            players.records, ssp_stats.player_records, diff
        );
    }

    Ok(())
}

/// The SSP file's name is date-stamped (`ratings260801.ssp`) and moves as
/// new snapshots land, so find it by extension rather than hardcoding the
/// name.
// Prefer the consolidated layout; an explicitly supplied legacy assets directory
// remains compatible with existing command invocations.
fn data_path(root: &Path, canonical: &str, legacy: &str) -> PathBuf {
    let candidate = root.join(canonical);
    if candidate.exists() { candidate } else { root.join(legacy) }
}

fn find_ssp(assets_dir: &Path) -> std::io::Result<PathBuf> {
    let baseline = assets_dir.join("inputs/ratings260801.ssp");
    if baseline.is_file() { return Ok(baseline); }

    let mut candidates: Vec<PathBuf> = std::fs::read_dir(assets_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("ssp"))
        .collect();
    candidates.sort();
    candidates.pop().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::NotFound,
            format!("no *.ssp file found in {}", assets_dir.display()),
        )
    })
}
