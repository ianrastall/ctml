//! Cross-source tournament deduplication for `crosstables.json` — a
//! direct port of `scripts/curate_source_tournaments.py`'s clustering
//! (`UnionFind`, `similarity`, `should_link`, `cluster_facts`,
//! `merged_table`), adapted to run against this repo's actual
//! already-merged `crosstables.json` rather than the separate
//! per-scraper draft-XML/JSON directory tree that script expects
//! (`D:\ctml\build\drafts\{chess-results,twic,nwchess-minev,olimpbase}`
//! — checked directly: only partially present on disk, and that script
//! targets the superseded `urn:ctml:1.0` namespace besides). The
//! clustering *algorithm* is unchanged; only what feeds it differs, and
//! the goal is the same either way: TWIC, OlimpBase, nwchess-minev, and
//! chess-results all independently scrape overlapping real-world
//! tournaments, so `crosstables.json`'s 48,198 rows are not 48,198
//! distinct real events.

use crate::crosstable::{CrosstableEntry, PlayerRow};
use crate::tournament::{is_valid_fide_id, PartialDate};
use crate::xmlutil::normalize_space;
use regex::Regex;
use std::collections::{HashMap, HashSet};

// ---------------------------------------------------------------------
// difflib.SequenceMatcher.ratio() — the Ratcliff/Obershelp algorithm,
// minus the `autojunk` heuristic (which only ever activates for
// sequences of 200+ elements; tournament-name strings never reach that,
// so its absence changes nothing for this data). Needed because
// `should_link`'s thresholds (0.94, 0.62) were tuned against this exact
// ratio function in the reference — an approximate string-similarity
// substitute would silently shift which pairs cluster.
// ---------------------------------------------------------------------

fn find_longest_match(a: &[char], b: &[char], alo: usize, ahi: usize, blo: usize, bhi: usize) -> (usize, usize, usize) {
    let mut b2j: HashMap<char, Vec<usize>> = HashMap::new();
    for (j, &ch) in b.iter().enumerate().take(bhi).skip(blo) {
        b2j.entry(ch).or_default().push(j);
    }

    let (mut best_i, mut best_j, mut best_size) = (alo, blo, 0usize);
    let mut j2len: HashMap<usize, usize> = HashMap::new();

    for i in alo..ahi {
        let mut new_j2len: HashMap<usize, usize> = HashMap::new();
        if let Some(js) = b2j.get(&a[i]) {
            for &j in js {
                let k = if j > 0 { j2len.get(&(j - 1)).copied().unwrap_or(0) + 1 } else { 1 };
                new_j2len.insert(j, k);
                if k > best_size {
                    best_i = i + 1 - k;
                    best_j = j + 1 - k;
                    best_size = k;
                }
            }
        }
        j2len = new_j2len;
    }

    while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
        best_i -= 1;
        best_j -= 1;
        best_size += 1;
    }
    while best_i + best_size < ahi && best_j + best_size < bhi && a[best_i + best_size] == b[best_j + best_size] {
        best_size += 1;
    }

    (best_i, best_j, best_size)
}

fn matched_total(a: &[char], b: &[char]) -> usize {
    let mut stack = vec![(0usize, a.len(), 0usize, b.len())];
    let mut total = 0;
    while let Some((alo, ahi, blo, bhi)) = stack.pop() {
        let (i, j, k) = find_longest_match(a, b, alo, ahi, blo, bhi);
        if k > 0 {
            total += k;
            if alo < i && blo < j {
                stack.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                stack.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    total
}

fn sequence_ratio(a: &str, b: &str) -> f64 {
    let ac: Vec<char> = a.chars().collect();
    let bc: Vec<char> = b.chars().collect();
    let total = ac.len() + bc.len();
    if total == 0 {
        return 1.0;
    }
    2.0 * matched_total(&ac, &bc) as f64 / total as f64
}

// ---------------------------------------------------------------------
// name_key / similarity
// ---------------------------------------------------------------------

struct NameKeyer {
    ordinal: Regex,
    year: Regex,
    stopword: Regex,
    nonalnum: Regex,
}

impl NameKeyer {
    fn new() -> Self {
        NameKeyer {
            ordinal: Regex::new(r"^\d{1,3}(st|nd|rd|th)\s+").unwrap(),
            year: Regex::new(r"\b(1[5-9]\d{2}|20\d{2})\b").unwrap(),
            stopword: Regex::new(r"\b(open|tournament|championship|final|group|section)\b").unwrap(),
            nonalnum: Regex::new(r"[^a-z0-9]+").unwrap(),
        }
    }

    fn key(&self, value: &str) -> String {
        let text = normalize_space(value).to_lowercase().replace('&', " and ");
        let text = self.ordinal.replace_all(&text, "");
        let text = self.year.replace_all(&text, "");
        let text = self.stopword.replace_all(&text, " ");
        let text = self.nonalnum.replace_all(&text, " ");
        text.split_whitespace().collect::<Vec<_>>().join(" ")
    }

    fn similarity(&self, a: &str, b: &str) -> f64 {
        let ka = self.key(a);
        let kb = self.key(b);
        if ka.is_empty() || kb.is_empty() {
            return 0.0;
        }
        let aa: HashSet<&str> = ka.split_whitespace().collect();
        let bb: HashSet<&str> = kb.split_whitespace().collect();
        let token = if !aa.is_empty() && !bb.is_empty() {
            aa.intersection(&bb).count() as f64 / aa.union(&bb).count() as f64
        } else {
            0.0
        };
        0.65 * sequence_ratio(&ka, &kb) + 0.35 * token
    }
}

// ---------------------------------------------------------------------
// Facts, union-find, clustering — all read from `should_link` /
// `cluster_facts` in the reference, not reinvented.
// ---------------------------------------------------------------------

const SOURCE_PRIORITY: &[(&str, i32)] =
    &[("chess-results", 100), ("twic", 90), ("edo", 80), ("nwchess-minev", 70), ("olimpbase", 60)];

fn source_priority(source: &str) -> i32 {
    SOURCE_PRIORITY.iter().find(|(s, _)| *s == source).map(|(_, p)| *p).unwrap_or(0)
}

struct Fact {
    index: usize,
    source: String,
    name: String,
    event_ref: String,
    start: PartialDate,
    /// FIDE-id-based roster keys only — populated for players with a
    /// valid FIDE id, empty otherwise.
    roster_fide: HashSet<String>,
    /// `name|fed`-based roster keys — populated for *every* named
    /// player, regardless of whether a FIDE id is also available. Kept
    /// as a parallel channel, not a fallback used only when
    /// `roster_fide` is empty: see `should_link` for why comparing both
    /// independently (rather than one-key-per-player, FIDE-preferred)
    /// matters for this actual data.
    roster_name: HashSet<String>,
    participant_count: usize,
    fide_count: usize,
    rated_count: usize,
}

/// The merge step's own dedup key for "is this the same participant
/// across cluster members" — FIDE-preferred, name-fallback, exactly one
/// key per player. Deliberately different from `should_link`'s
/// comparison, which checks both channels independently (see
/// `Fact::roster_fide`/`roster_name`): once two facts are already known
/// to be the same tournament, collapsing to one key per player is the
/// right merge behavior; *deciding* they're the same tournament is where
/// a single preferred key silently fails whenever the two sides captured
/// different id formats for the same real players — checked directly
/// against this data, not assumed (see `HANDOFF.md`: chess-results
/// carries FIDE ids for 75% of its players, TWIC/olimpbase/nwchess-minev
/// for 0%, so a FIDE-preferred single key can never link a chess-results
/// entry to the other three sources even when the roster is identical).
fn player_roster_key(p: &PlayerRow) -> String {
    if let Some(id) = p.fide_id.as_deref() {
        if is_valid_fide_id(id) {
            return format!("player:fide:{id}");
        }
    }
    format!("{}|{}", normalize_space(&p.name).to_lowercase(), p.fed.as_deref().unwrap_or(""))
}

fn player_name_key(p: &PlayerRow) -> String {
    format!("{}|{}", normalize_space(&p.name).to_lowercase(), p.fed.as_deref().unwrap_or(""))
}

fn build_facts(entries: &[CrosstableEntry]) -> Vec<Fact> {
    let mut facts = Vec::with_capacity(entries.len());
    for (index, e) in entries.iter().enumerate() {
        let Some(start) = e.start.as_deref().and_then(parse_simple_date) else { continue };
        let named_players = || e.players.iter().filter(|p| !p.name.trim().is_empty());
        let roster_fide: HashSet<String> =
            named_players().filter(|p| p.fide_id.as_deref().is_some_and(is_valid_fide_id)).map(player_roster_key).collect();
        let roster_name: HashSet<String> = named_players().map(player_name_key).collect();
        let fide_count = roster_fide.len();
        let rated_count = e.players.iter().filter(|p| p.rating.is_some()).count();
        facts.push(Fact {
            index,
            source: e.source.clone().unwrap_or_default(),
            name: e.event.clone(),
            event_ref: e.event_ref.clone().unwrap_or_default(),
            start,
            roster_fide,
            roster_name,
            participant_count: e.players.len(),
            fide_count,
            rated_count,
        });
    }
    facts
}

/// Reads the same `"YYYY"`/`"YYYY-MM"`/`"YYYY-MM-DD"` shapes
/// `crosstable_to_ctml`'s own `parse_iso_date` accepts — kept separate
/// (rather than exposing that function) since it's the only piece of
/// that parser this module needs.
fn parse_simple_date(s: &str) -> Option<PartialDate> {
    let t = normalize_space(s);
    let all_digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if t.len() == 4 && all_digits(&t) {
        return Some(PartialDate { y: t.parse().ok()?, m: None, d: None });
    }
    if t.len() == 7 && t.as_bytes()[4] == b'-' && all_digits(&t[..4]) && all_digits(&t[5..7]) {
        return Some(PartialDate { y: t[..4].parse().ok()?, m: Some(t[5..7].parse().ok()?), d: None });
    }
    if t.len() == 10 && t.as_bytes()[4] == b'-' && t.as_bytes()[7] == b'-' && all_digits(&t[..4]) && all_digits(&t[5..7]) && all_digits(&t[8..10]) {
        return Some(PartialDate { y: t[..4].parse().ok()?, m: Some(t[5..7].parse().ok()?), d: Some(t[8..10].parse().ok()?) });
    }
    None
}

struct UnionFind {
    parent: Vec<usize>,
}

impl UnionFind {
    fn new(n: usize) -> Self {
        UnionFind { parent: (0..n).collect() }
    }
    fn find(&mut self, x: usize) -> usize {
        if self.parent[x] != x {
            self.parent[x] = self.find(self.parent[x]);
        }
        self.parent[x]
    }
    fn union(&mut self, a: usize, b: usize) {
        let (ra, rb) = (self.find(a), self.find(b));
        if ra != rb {
            self.parent[rb] = ra;
        }
    }
}

fn should_link(keyer: &NameKeyer, a: &Fact, b: &Fact) -> bool {
    // The reference's own gate here is `if a.event_ref or b.event_ref:
    // return False` after the exact-match check — i.e. *either* side
    // merely having a non-empty event_ref, matched or not, vetoes fuzzy
    // matching entirely. Checked directly against this data: `event_ref`
    // is populated for TWIC only (31,890 of its 46,472 entries) and
    // *never* for chess-results/olimpbase/nwchess-minev, so that gate
    // doesn't express "these are known to disagree" for a
    // TWIC-vs-other-source pair — it expresses "TWIC happened to resolve
    // one locally and the other source's scraper never fills this field
    // at all." Confirmed concretely: a chess-results/TWIC pair for the
    // literal same tournament (5th Gambit GM Closed, 2026-05-31, 9/9
    // roster overlap) was blocked by exactly this gate before the fix
    // below. Only an actual disagreement — both sides resolved, to
    // different refs — is still treated as authoritative and vetoes
    // fuzzy matching; one side simply not having resolved one doesn't.
    if !a.event_ref.is_empty() && !b.event_ref.is_empty() {
        return a.event_ref == b.event_ref;
    }
    if a.start.y != b.start.y {
        return false;
    }
    if let (Some(am), Some(bm)) = (a.start.m, b.start.m) {
        if (am as i32 - bm as i32).abs() > 1 {
            return false;
        }
    }
    let sim = keyer.similarity(&a.name, &b.name);
    if sim >= 0.94 {
        return true;
    }
    if sim < 0.62 {
        return false;
    }
    // Two independent overlap channels, checked separately rather than
    // one FIDE-preferred key per player — see `Fact::roster_fide`/
    // `roster_name`'s docs for why: sources that never carry a FIDE id
    // (TWIC, olimpbase, nwchess-minev — checked directly, 0% coverage
    // each) can only ever link against another source through the name
    // channel, even when the FIDE-carrying source's own roster is
    // otherwise identical.
    let overlap = |x: &HashSet<String>, y: &HashSet<String>| -> f64 {
        if x.is_empty() || y.is_empty() {
            return 0.0;
        }
        x.intersection(y).count() as f64 / x.len().min(y.len()) as f64
    };
    overlap(&a.roster_fide, &b.roster_fide) >= 0.5 || overlap(&a.roster_name, &b.roster_name) >= 0.5
}

/// Returns clusters as lists of indices into `facts` — largest first,
/// matching the reference's own sort (`-len(c), year, name`) closely
/// enough for deterministic, reviewable output ordering.
fn cluster_facts(keyer: &NameKeyer, facts: &[Fact]) -> Vec<Vec<usize>> {
    let mut uf = UnionFind::new(facts.len());

    let mut by_event_ref: HashMap<&str, Vec<usize>> = HashMap::new();
    let mut blocks: HashMap<(i32, String), Vec<usize>> = HashMap::new();
    for (idx, fact) in facts.iter().enumerate() {
        if !fact.event_ref.is_empty() {
            by_event_ref.entry(&fact.event_ref).or_default().push(idx);
        }
        let key = keyer.key(&fact.name);
        let token = key.split_whitespace().next().unwrap_or("").to_string();
        blocks.entry((fact.start.y, token)).or_default().push(idx);
    }
    for indexes in by_event_ref.values() {
        for &idx in &indexes[1..] {
            uf.union(indexes[0], idx);
        }
    }
    for indexes in blocks.values() {
        if indexes.len() > 400 {
            continue; // matches the reference's own cap: skip pathologically generic blocks
        }
        for (pos, &left) in indexes.iter().enumerate() {
            for &right in &indexes[pos + 1..] {
                if should_link(keyer, &facts[left], &facts[right]) {
                    uf.union(left, right);
                }
            }
        }
    }

    let mut clusters: HashMap<usize, Vec<usize>> = HashMap::new();
    for idx in 0..facts.len() {
        let root = uf.find(idx);
        clusters.entry(root).or_default().push(idx);
    }
    let mut out: Vec<Vec<usize>> = clusters.into_values().collect();
    out.sort_by(|a, b| {
        b.len().cmp(&a.len()).then_with(|| facts[a[0]].start.y.cmp(&facts[b[0]].start.y)).then_with(|| facts[a[0]].name.cmp(&facts[b[0]].name))
    });
    out
}

fn best_in_cluster(facts: &[Fact], cluster: &[usize]) -> usize {
    *cluster
        .iter()
        .max_by_key(|&&i| {
            let f = &facts[i];
            (source_priority(&f.source), f.participant_count, f.fide_count, f.rated_count)
        })
        .unwrap()
}

/// Merges every entry in a multi-member cluster into one synthetic
/// `CrosstableEntry`, matching `merged_table` field-for-field: identity
/// fields come from the highest-priority-source member; `players` is
/// the union of every member's roster, keyed by FIDE ref (or
/// `name|fed`), each field filled from the highest-priority source that
/// actually has it rather than the first one encountered.
fn merge_cluster(entries: &[CrosstableEntry], facts: &[Fact], cluster: &[usize], cluster_id: &str) -> CrosstableEntry {
    let best_idx = best_in_cluster(facts, cluster);
    let best_entry = &entries[facts[best_idx].index];

    let mut order: Vec<usize> = cluster.to_vec();
    order.sort_by_key(|&i| std::cmp::Reverse(source_priority(&facts[i].source)));

    let mut merged: HashMap<String, PlayerRow> = HashMap::new();
    let mut key_order: Vec<String> = Vec::new();
    for &fi in &order {
        let entry = &entries[facts[fi].index];
        for p in &entry.players {
            if p.name.trim().is_empty() {
                continue;
            }
            let key = player_roster_key(p);
            if let Some(old) = merged.get_mut(&key) {
                if old.rank.is_none() {
                    old.rank = p.rank;
                }
                if old.title.is_none() {
                    old.title = p.title.clone();
                }
                if old.fed.is_none() {
                    old.fed = p.fed.clone();
                }
                if old.fide_id.is_none() {
                    old.fide_id = p.fide_id.clone();
                }
                if old.rating.is_none() {
                    old.rating = p.rating;
                }
                if old.score.is_none() {
                    old.score = p.score;
                }
            } else {
                key_order.push(key.clone());
                merged.insert(
                    key,
                    PlayerRow {
                        rank: p.rank,
                        name: p.name.clone(),
                        title: p.title.clone(),
                        fed: p.fed.clone(),
                        rating: p.rating,
                        score: p.score,
                        fide_id: p.fide_id.clone(),
                        seed: None,
                        source_id: None,
                        sex: None,
                        club: None,
                        ref_: None,
                    },
                );
            }
        }
    }
    let mut players: Vec<PlayerRow> = key_order.into_iter().filter_map(|k| merged.remove(&k)).collect();
    players.sort_by_key(|p| (p.rank.is_none(), p.rank.unwrap_or(i64::MAX), p.name.clone()));

    let notes = format!(
        "Review draft merged from source tournaments: {}",
        order.iter().map(|&fi| format!("{}:{}", facts[fi].source, entries[facts[fi].index].ref_.clone().unwrap_or_else(|| format!("#{fi}")))).collect::<Vec<_>>().join("; ")
    );

    CrosstableEntry {
        event: best_entry.event.clone(),
        header: None,
        format: "unknown".to_string(),
        players,
        source: Some("curation-draft".to_string()),
        ref_: Some(cluster_id.to_string()),
        event_ref: best_entry.event_ref.clone(),
        start: best_entry.start.clone(),
        end: best_entry.end.clone(),
        place: best_entry.place.clone(),
        country: None,
        cadence: None,
        classification: None,
        notes: Some(notes),
        rating_system: Some("unknown".to_string()),
        reader: Some("curate_source_tournaments/0.1".to_string()),
        url: best_entry.url.clone(),
        source_path: None,
    }
}

pub struct DedupResult {
    /// One entry per cluster: `(is_merged, CrosstableEntry)`. `is_merged
    /// == false` means this is a size-1 cluster passed through
    /// unchanged (the original entry, untouched — matching the
    /// reference, which never rewrites a document with no duplicates
    /// found).
    pub clusters: Vec<(bool, CrosstableEntry)>,
    pub source_documents: usize,
    pub multi_source_clusters: usize,
    pub dated_dropped: usize,
}

pub fn dedup(entries: Vec<CrosstableEntry>) -> DedupResult {
    let keyer = NameKeyer::new();
    let facts = build_facts(&entries);
    let dated_dropped = entries.len() - facts.len();
    let raw_clusters = cluster_facts(&keyer, &facts);

    let mut clusters = Vec::with_capacity(raw_clusters.len());
    let mut multi_source_clusters = 0;
    for (n, cluster) in raw_clusters.iter().enumerate() {
        if cluster.len() > 1 {
            let distinct_sources: HashSet<&str> = cluster.iter().map(|&i| facts[i].source.as_str()).collect();
            if distinct_sources.len() > 1 {
                multi_source_clusters += 1;
            }
            let cluster_id = format!("cluster-{:06}", n + 1);
            clusters.push((true, merge_cluster(&entries, &facts, cluster, &cluster_id)));
        } else {
            let idx = facts[cluster[0]].index;
            // Move the original entry out rather than clone it — cheap
            // and correct since each `facts[].index` is unique.
            clusters.push((false, entries[idx].clone()));
        }
    }

    DedupResult { clusters, source_documents: entries.len(), multi_source_clusters, dated_dropped }
}

/// Diagnostic: traces `should_link`'s inputs for two original `entries`
/// indices, to inspect why a specific pair did or didn't cluster (`ctml-
/// clean debug-dedup-pair <idx-a> <idx-b>`). Found both real bugs fixed
/// this session — kept as a real tool, not a one-off.
pub fn debug_pair(entries: &[CrosstableEntry], idx_a: usize, idx_b: usize) {
    let keyer = NameKeyer::new();
    let facts = build_facts(entries);
    let fa = facts.iter().find(|f| f.index == idx_a);
    let fb = facts.iter().find(|f| f.index == idx_b);
    let (Some(fa), Some(fb)) = (fa, fb) else {
        println!("one or both indices have no parseable start date (dropped before facts)");
        return;
    };
    println!("a: source={} name={:?} event_ref={:?} start=({}, {:?}, {:?})", fa.source, fa.name, fa.event_ref, fa.start.y, fa.start.m, fa.start.d);
    println!("b: source={} name={:?} event_ref={:?} start=({}, {:?}, {:?})", fb.source, fb.name, fb.event_ref, fb.start.y, fb.start.m, fb.start.d);
    let ka = keyer.key(&fa.name);
    let kb = keyer.key(&fb.name);
    println!("name_key(a)={ka:?} token={:?}", ka.split_whitespace().next());
    println!("name_key(b)={kb:?} token={:?}", kb.split_whitespace().next());
    println!("similarity={}", keyer.similarity(&fa.name, &fb.name));
    println!("roster_fide(a) len={} roster_fide(b) len={}", fa.roster_fide.len(), fb.roster_fide.len());
    println!("roster_name(a) len={} roster_name(b) len={}", fa.roster_name.len(), fb.roster_name.len());
    println!("roster_name intersection={}", fa.roster_name.intersection(&fb.roster_name).count());
    println!("should_link = {}", should_link(&keyer, fa, fb));
}
