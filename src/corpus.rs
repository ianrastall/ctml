//! Dedup-safe writer for CTML tournament documents — a direct port of
//! `D:\dev\proj\ctml\readers\corpus_writer.py`. Two identity levels, read
//! from that file, not redesigned:
//!
//! - **Tournament-level**: one file per event ref. A second import run
//!   resolving to the same event ref lands in the same file.
//! - **Game-level**: identity is `(white ref, black ref, round)` — the
//!   real "one game per board per round per pairing" slot a tournament
//!   has. Deliberately not the trajectory fingerprint alone (a short
//!   aborted game would fingerprint-collide with any other equally short
//!   game between different players): the slot is identity, the
//!   fingerprint confirms/distinguishes within a slot. No existing game
//!   in the slot -> append. Fingerprints match (or existing lacks one) ->
//!   already recorded, enrich if the incoming side has a fingerprint the
//!   existing one lacks. Fingerprints differ -> genuine divergence,
//!   logged and left untouched — first-recorded data wins, never
//!   silently overwritten.
//!
//! Implementation note: this mutates existing files by text splicing, not
//! a DOM tree, because every file this function ever reads was written by
//! this same emitter (`pgntournament::tournament_xml`) with consistent,
//! known formatting — a real DOM library would be needed to merge
//! arbitrary third-party XML, but isn't for merging this program's own
//! output back into itself, and quick_xml (a streaming reader/writer, not
//! a DOM library) is what's already a dependency.

use crate::tournament::PartialDate;
use crate::xmlutil::slug;
use quick_xml::events::Event;
use quick_xml::Reader;
use std::collections::HashMap;
use std::path::{Path, PathBuf};

pub fn event_ref_filename(event_ref: &str) -> String {
    let body = event_ref.strip_prefix("event:").unwrap_or(event_ref);
    let cleaned: String =
        body.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' }).collect();
    format!("{cleaned}.xml")
}

struct HeaderInfo {
    name: String,
    start: Option<PartialDate>,
    end: Option<PartialDate>,
}

fn qn_local(name: &[u8]) -> &[u8] {
    name
}

/// Cheap first pass: just the header name and date range, for
/// `find_matching_file`'s scan over every file in the corpus directory.
fn read_header_info(path: &Path) -> Option<HeaderInfo> {
    let text = std::fs::read_to_string(path).ok()?;
    let mut xml = Reader::from_str(&text);
    xml.config_mut().trim_text(true);
    let mut buf = Vec::new();

    let mut name = None;
    let mut start = None;
    let mut end = None;
    let mut in_header_name = false;
    let mut in_start = false;
    let mut in_end = false;
    let mut depth_tag_stack: Vec<Vec<u8>> = Vec::new();

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let local = qn_local(e.local_name().as_ref()).to_vec();
                let parent = depth_tag_stack.last().map(|v| v.as_slice());
                if local == b"name" && parent == Some(b"header".as_slice()) {
                    in_header_name = true;
                } else if local == b"start" {
                    in_start = true;
                } else if local == b"end" {
                    in_end = true;
                } else if matches!(local.as_slice(), b"year" | b"month" | b"day") && (in_start || in_end) {
                    let y: Option<i32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"y")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    let m: Option<u32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"m")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    let d: Option<u32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"d")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    if let Some(y) = y {
                        let pd = PartialDate { y, m, d };
                        if in_start {
                            start = Some(pd);
                        } else if in_end {
                            end = Some(pd);
                        }
                    }
                }
                depth_tag_stack.push(local);
            }
            Ok(Event::Empty(e)) => {
                let local = qn_local(e.local_name().as_ref()).to_vec();
                if matches!(local.as_slice(), b"year" | b"month" | b"day") && (in_start || in_end) {
                    let y: Option<i32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"y")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    let m: Option<u32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"m")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    let d: Option<u32> = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"d")
                        .and_then(|a| a.unescape_value().ok())
                        .and_then(|v| v.parse().ok());
                    if let Some(y) = y {
                        let pd = PartialDate { y, m, d };
                        if in_start {
                            start = Some(pd);
                        } else if in_end {
                            end = Some(pd);
                        }
                    }
                }
            }
            Ok(Event::End(_)) => {
                if let Some(local) = depth_tag_stack.pop() {
                    if local == b"name" {
                        in_header_name = false;
                    } else if local == b"start" {
                        in_start = false;
                    } else if local == b"end" {
                        in_end = false;
                    }
                }
            }
            Ok(Event::Text(e)) => {
                if in_header_name && name.is_none() {
                    if let Ok(t) = e.unescape() {
                        name = Some(t.trim().to_string());
                    }
                }
            }
            Ok(_) => {}
            Err(_) => return None,
        }
        buf.clear();
        if name.is_some() && start.is_some() && end.is_some() {
            break; // have everything find_matching_file needs
        }
    }

    Some(HeaderInfo { name: name.unwrap_or_default(), start, end })
}

/// In-memory replacement for the reference's own `find_matching_file`,
/// which rescans and re-parses every `*.xml` in the corpus directory on
/// every single lookup (a real characteristic of the Python original,
/// not introduced here — confirmed by reading it). That's O(files²)
/// over a whole import run and was directly observed stalling out on a
/// 200MB PGN slice, quadratically slowing down as the corpus grew past a
/// couple thousand files. There's no reason to inherit that cost: the
/// matching *rule* (same name slug, overlapping or near date range) is
/// unchanged from the reference, only *how many times the disk gets
/// read* changes — built once per run (`build`), then updated in memory
/// as files are created or broadened (`record`), so a lookup is a
/// hash-map fetch plus a scan of only same-slug candidates, not the
/// whole corpus.
pub struct CorpusIndex {
    by_slug: HashMap<String, Vec<(PathBuf, i64, i64)>>,
}

impl CorpusIndex {
    pub fn build(corpus_dir: &Path) -> std::io::Result<CorpusIndex> {
        let mut by_slug: HashMap<String, Vec<(PathBuf, i64, i64)>> = HashMap::new();
        if corpus_dir.exists() {
            for entry in std::fs::read_dir(corpus_dir)?.flatten() {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) != Some("xml") {
                    continue;
                }
                if let Some(info) = read_header_info(&path) {
                    if let (Some(s), Some(e)) = (&info.start, &info.end) {
                        by_slug.entry(slug(&info.name, "unknown")).or_default().push((
                            path,
                            s.approx_epoch_day(),
                            e.approx_epoch_day(),
                        ));
                    }
                }
            }
        }
        Ok(CorpusIndex { by_slug })
    }

    fn find_matching(&self, event_name: &str, start_day: i64, end_day: i64, max_gap_days: i64) -> Option<PathBuf> {
        let target = slug(event_name, "unknown");
        let candidates = self.by_slug.get(&target)?;
        for (path, es, ee) in candidates {
            if start_day <= *ee && *es <= end_day {
                return Some(path.clone());
            }
            let gap = (start_day - ee).max(es - end_day);
            if gap <= max_gap_days {
                return Some(path.clone());
            }
        }
        None
    }

    /// Records a file's (possibly just-broadened) date coverage so a
    /// later lookup in the *same run* sees it without touching disk.
    fn record(&mut self, path: &Path, event_name: &str, start_day: i64, end_day: i64) {
        let key = slug(event_name, "unknown");
        let entries = self.by_slug.entry(key).or_default();
        if let Some(existing) = entries.iter_mut().find(|(p, _, _)| p == path) {
            existing.1 = existing.1.min(start_day);
            existing.2 = existing.2.max(end_day);
        } else {
            entries.push((path.to_path_buf(), start_day, end_day));
        }
    }
}

struct ExistingGame {
    trajectory: Option<String>,
}

/// Full extraction pass over an existing tournament file: participant
/// `ref -> id`, highest existing `pNNNN` id number (for allocating new
/// ones), and every game's `(white_ref, black_ref, round) -> trajectory`
/// slot.
fn extract_existing(text: &str) -> (HashMap<String, String>, u32, HashMap<(Option<String>, Option<String>, String), ExistingGame>) {
    let mut xml = Reader::from_str(text);
    xml.config_mut().trim_text(true);
    let mut buf = Vec::new();

    let mut ref_to_id: HashMap<String, String> = HashMap::new();
    let mut id_to_ref: HashMap<String, String> = HashMap::new();
    let mut max_id = 0u32;
    let mut slots: HashMap<(Option<String>, Option<String>, String), ExistingGame> = HashMap::new();

    let mut stack: Vec<Vec<u8>> = Vec::new();
    let mut cur_participant_id: Option<String> = None;
    let mut cur_game_white: Option<String> = None;
    let mut cur_game_black: Option<String> = None;
    let mut cur_game_round: Option<String> = None;
    let mut cur_trajectory: Option<String> = None;

    // `enter` runs for both Start and Empty (attribute handling is
    // identical either way); only Start pushes onto `stack`, since Empty
    // never gets a matching End event. Keeping these as two explicit
    // match arms — never a combined `Start(e) | Empty(e)` pattern — is
    // deliberate: that combined form can't tell the two apart for stack
    // bookkeeping, which corrupted a parse earlier this session
    // (`xmlplayers.rs`) the same way it would here.
    macro_rules! enter {
        ($e:expr) => {{
            let e = $e;
            let local = e.local_name().as_ref().to_vec();
            match local.as_slice() {
                b"participant" => {
                    cur_participant_id = e
                        .attributes()
                        .flatten()
                        .find(|a| a.key.as_ref() == b"id")
                        .and_then(|a| a.unescape_value().ok())
                        .map(|v| v.into_owned());
                    if let Some(id) = &cur_participant_id {
                        if let Some(num) = id.strip_prefix('p').and_then(|s| s.parse::<u32>().ok()) {
                            max_id = max_id.max(num);
                        }
                    }
                }
                b"playerRef" => {
                    if let Some(r) =
                        e.attributes().flatten().find(|a| a.key.as_ref() == b"ref").and_then(|a| a.unescape_value().ok())
                    {
                        if let Some(id) = &cur_participant_id {
                            ref_to_id.insert(r.clone().into_owned(), id.clone());
                            id_to_ref.insert(id.clone(), r.into_owned());
                        }
                    }
                }
                b"game" => {
                    let get = |k: &[u8]| {
                        e.attributes().flatten().find(|a| a.key.as_ref() == k).and_then(|a| a.unescape_value().ok()).map(|v| v.into_owned())
                    };
                    cur_game_white = get(b"white");
                    cur_game_black = get(b"black");
                    cur_game_round = get(b"round");
                    cur_trajectory = None;
                }
                b"fingerprint" => {
                    let scope = e.attributes().flatten().find(|a| a.key.as_ref() == b"scope").and_then(|a| a.unescape_value().ok());
                    if scope.as_deref() == Some("trajectory") {
                        cur_trajectory = e
                            .attributes()
                            .flatten()
                            .find(|a| a.key.as_ref() == b"value")
                            .and_then(|a| a.unescape_value().ok())
                            .map(|v| v.into_owned());
                    }
                }
                _ => {}
            }
            local
        }};
    }

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let local = enter!(&e);
                stack.push(local);
            }
            Ok(Event::Empty(e)) => {
                enter!(&e); // no stack push: Empty has no matching End
            }
            Ok(Event::End(_)) => {
                if let Some(local) = stack.pop() {
                    if local == b"game" {
                        let w = cur_game_white.take().and_then(|id| id_to_ref.get(&id).cloned());
                        let b = cur_game_black.take().and_then(|id| id_to_ref.get(&id).cloned());
                        let round = cur_game_round.take().unwrap_or_default();
                        slots.insert((w, b, round), ExistingGame { trajectory: cur_trajectory.take() });
                    }
                }
            }
            Ok(_) => {}
            Err(_) => break,
        }
        buf.clear();
    }

    (ref_to_id, max_id, slots)
}

/// Extracts `<ctml:TAG id="ID">...</ctml:TAG>`-shaped blocks (verbatim,
/// including the enclosing tags) from XML text this program generated
/// itself, keyed by their `id`/ref-bearing attribute — used to pull
/// participant/game blocks out of the freshly-generated incoming
/// tournament string so they can be re-spliced into an existing file
/// without re-deriving their contents.
fn extract_blocks(text: &str, open_prefix: &str, close_tag: &str) -> Vec<(String, String)> {
    let mut out = Vec::new();
    let mut pos = 0;
    while let Some(start) = text[pos..].find(open_prefix) {
        let abs_start = pos + start;
        let Some(close_rel) = text[abs_start..].find(close_tag) else { break };
        let abs_end = abs_start + close_rel + close_tag.len();
        out.push((String::new(), text[abs_start..abs_end].to_string()));
        pos = abs_end;
    }
    out
}

fn attr_value(tag_open: &str, attr: &str) -> Option<String> {
    let needle = format!(r#"{attr}=""#);
    let start = tag_open.find(&needle)? + needle.len();
    let end = tag_open[start..].find('"')? + start;
    Some(tag_open[start..end].to_string())
}

pub struct MergeReport {
    pub status: &'static str,
    pub added_games: u32,
    pub added_participants: u32,
    pub matched_same: u32,
    pub enriched: u32,
    pub divergences: u32,
}

/// Writes a fresh file, or merges into an existing one for the same
/// tournament. `incoming_xml` must be exactly what
/// `pgntournament::tournament_xml` produced (this function relies on
/// that known formatting to splice text, not a general XML merge).
/// `index` is built once per run via [`CorpusIndex::build`] and updated
/// here — see that type's docs for why this isn't a fresh directory scan
/// per call.
pub fn merge_tournament(
    corpus_dir: &Path,
    incoming_xml: &str,
    event_ref: &str,
    max_gap_days: i64,
    index: &mut CorpusIndex,
    log: &mut dyn FnMut(String),
) -> std::io::Result<MergeReport> {
    std::fs::create_dir_all(corpus_dir)?;
    let mut path = corpus_dir.join(event_ref_filename(event_ref));

    let name = extract_tag_text(incoming_xml, "name").unwrap_or_default();
    let start_day = extract_first_date(incoming_xml, "start").map(|d| d.approx_epoch_day());
    let end_day = extract_first_date(incoming_xml, "end").map(|d| d.approx_epoch_day());

    if !path.exists() {
        // Try to find an existing file for the same real tournament under
        // a different (previously-synthesized) filename.
        if let (Some(sd), Some(ed)) = (start_day, end_day) {
            if let Some(matched) = index.find_matching(&name, sd, ed, max_gap_days) {
                path = matched;
            }
        }
    }

    if !path.exists() {
        std::fs::write(&path, incoming_xml)?;
        if let (Some(sd), Some(ed)) = (start_day, end_day) {
            index.record(&path, &name, sd, ed);
        }
        return Ok(MergeReport { status: "created", added_games: 0, added_participants: 0, matched_same: 0, enriched: 0, divergences: 0 });
    }

    let mut existing_text = std::fs::read_to_string(&path)?;
    let (mut ref_to_id, mut max_id, existing_slots) = extract_existing(&existing_text);

    let incoming_participant_blocks = extract_blocks(incoming_xml, "    <ctml:participant id=\"", "    </ctml:participant>");
    let incoming_game_blocks = extract_blocks(incoming_xml, "    <ctml:game ", "    </ctml:game>");

    // incoming id -> ref, and id -> block text, from the blocks just extracted.
    let mut incoming_id_to_ref: HashMap<String, String> = HashMap::new();
    let mut incoming_id_to_block: HashMap<String, String> = HashMap::new();
    for (_, block) in &incoming_participant_blocks {
        let open_end = block.find('>').map(|i| i + 1).unwrap_or(block.len());
        let open_tag = &block[..open_end];
        if let (Some(id), Some(pref)) = (attr_value(open_tag, "id"), extract_first_attr(block, "playerRef", "ref")) {
            incoming_id_to_ref.insert(id.clone(), pref);
            incoming_id_to_block.insert(id, block.clone());
        }
    }

    let mut added_participants = 0u32;
    let mut added_games = 0u32;
    let mut matched_same = 0u32;
    let mut enriched = 0u32;
    let mut divergences = 0u32;
    let mut new_participant_blocks: Vec<String> = Vec::new();
    let mut new_game_blocks: Vec<String> = Vec::new();

    /// Not a closure over `ref_to_id`, deliberately: the enrichment path
    /// below also needs to *read* `ref_to_id` within the same loop, and a
    /// `FnMut` closure capturing it would hold a mutable borrow for the
    /// whole loop body, conflicting with that read. An explicit function
    /// taking `&mut` only for the duration of each call avoids that.
    fn ensure_participant(
        ref_opt: &Option<String>,
        ref_to_id: &mut HashMap<String, String>,
        incoming_id_to_ref: &HashMap<String, String>,
        incoming_id_to_block: &HashMap<String, String>,
        max_id: &mut u32,
        new_participant_blocks: &mut Vec<String>,
        added_participants: &mut u32,
    ) -> Option<String> {
        let r = ref_opt.as_ref()?;
        if let Some(id) = ref_to_id.get(r) {
            return Some(id.clone());
        }
        let (src_id, _) = incoming_id_to_ref.iter().find(|(_, v)| *v == r)?;
        let src_block = incoming_id_to_block.get(src_id)?;
        *max_id += 1;
        let new_id = format!("p{max_id:04}");
        let new_block = src_block.replacen(&format!(r#"id="{src_id}""#), &format!(r#"id="{new_id}""#), 1);
        new_participant_blocks.push(new_block);
        ref_to_id.insert(r.clone(), new_id.clone());
        *added_participants += 1;
        Some(new_id)
    }

    for (_, block) in &incoming_game_blocks {
        let open_end = block.find('>').map(|i| i + 1).unwrap_or(block.len());
        let open_tag = &block[..open_end];
        let w_id = attr_value(open_tag, "white");
        let b_id = attr_value(open_tag, "black");
        let round = attr_value(open_tag, "round").unwrap_or_default();
        let w_ref = w_id.as_ref().and_then(|id| incoming_id_to_ref.get(id)).cloned();
        let b_ref = b_id.as_ref().and_then(|id| incoming_id_to_ref.get(id)).cloned();
        let slot = (w_ref.clone(), b_ref.clone(), round.clone());

        match existing_slots.get(&slot) {
            None => {
                let new_w = ensure_participant(
                    &w_ref,
                    &mut ref_to_id,
                    &incoming_id_to_ref,
                    &incoming_id_to_block,
                    &mut max_id,
                    &mut new_participant_blocks,
                    &mut added_participants,
                );
                let new_b = ensure_participant(
                    &b_ref,
                    &mut ref_to_id,
                    &incoming_id_to_ref,
                    &incoming_id_to_block,
                    &mut max_id,
                    &mut new_participant_blocks,
                    &mut added_participants,
                );
                let mut new_block = block.clone();
                if let Some(id) = &new_w {
                    if let Some(old) = &w_id {
                        new_block = new_block.replacen(&format!(r#"white="{old}""#), &format!(r#"white="{id}""#), 1);
                    }
                }
                if let Some(id) = &new_b {
                    if let Some(old) = &b_id {
                        new_block = new_block.replacen(&format!(r#"black="{old}""#), &format!(r#"black="{id}""#), 1);
                    }
                }
                new_game_blocks.push(new_block);
                added_games += 1;
            }
            Some(existing) => {
                let incoming_traj = extract_trajectory_value(block);
                match (&existing.trajectory, &incoming_traj) {
                    (None, Some(_)) => {
                        enriched += 1;
                        // Splice a fingerprints block into the existing
                        // game's text — see module docs: text splicing is
                        // sound here because both sides are this
                        // program's own known format. Located by the
                        // existing file's own (white_id, black_id, round)
                        // — round alone is never unique within a
                        // tournament (every board in a round shares it).
                        if let Some(fp_block) = extract_fingerprints_block(block) {
                            let existing_w_id = w_ref.as_ref().and_then(|r| ref_to_id.get(r)).cloned();
                            let existing_b_id = b_ref.as_ref().and_then(|r| ref_to_id.get(r)).cloned();
                            if let (Some(wid), Some(bid)) = (existing_w_id, existing_b_id) {
                                existing_text =
                                    splice_fingerprints_into_existing_game(&existing_text, &round, &wid, &bid, &fp_block);
                            }
                        }
                    }
                    (Some(e), Some(i)) if e != i => {
                        divergences += 1;
                        log(format!(
                            "DIVERGENCE in {}: round={round} white={w_ref:?} black={b_ref:?} existing={}... incoming={}... -- keeping existing, incoming NOT merged",
                            path.display(),
                            &e[..16.min(e.len())],
                            &i[..16.min(i.len())]
                        ));
                    }
                    _ => matched_same += 1,
                }
            }
        }
    }

    if !new_participant_blocks.is_empty() {
        let marker = "  </ctml:participants>";
        if let Some(pos) = existing_text.find(marker) {
            existing_text.insert_str(pos, &new_participant_blocks.join(""));
        }
    }
    if !new_game_blocks.is_empty() {
        let marker = "  </ctml:games>";
        if let Some(pos) = existing_text.find(marker) {
            existing_text.insert_str(pos, &new_game_blocks.join(""));
        }
    }

    // Broaden recorded date coverage if the incoming batch extends past
    // what's on file, and keep the in-memory index in sync so a later
    // lookup in this same run sees the broadened range too.
    if let (Some(i_start), Some(i_end)) = (extract_first_date(incoming_xml, "start"), extract_first_date(incoming_xml, "end")) {
        if let (Some(e_start), Some(e_end)) = (extract_first_date(&existing_text, "start"), extract_first_date(&existing_text, "end")) {
            let mut broadened = false;
            if i_start.approx_epoch_day() < e_start.approx_epoch_day() {
                if let Some(new_block) = extract_dated_element(incoming_xml, "start") {
                    existing_text = replace_dated_element(&existing_text, "start", &new_block);
                    broadened = true;
                }
            }
            if i_end.approx_epoch_day() > e_end.approx_epoch_day() {
                if let Some(new_block) = extract_dated_element(incoming_xml, "end") {
                    existing_text = replace_dated_element(&existing_text, "end", &new_block);
                    broadened = true;
                }
            }
            if broadened {
                let existing_name = extract_tag_text(&existing_text, "name").unwrap_or_else(|| name.clone());
                let sd = extract_first_date(&existing_text, "start").map(|d| d.approx_epoch_day());
                let ed = extract_first_date(&existing_text, "end").map(|d| d.approx_epoch_day());
                if let (Some(sd), Some(ed)) = (sd, ed) {
                    index.record(&path, &existing_name, sd, ed);
                }
            }
        }
    }

    std::fs::write(&path, existing_text)?;

    Ok(MergeReport { status: "merged", added_games, added_participants, matched_same, enriched, divergences })
}

fn extract_tag_text(xml: &str, tag: &str) -> Option<String> {
    let open = format!("<ctml:{tag}>");
    let close = format!("</ctml:{tag}>");
    let start = xml.find(&open)? + open.len();
    let end = xml[start..].find(&close)? + start;
    Some(xml[start..end].trim().to_string())
}

/// Finds the first `<ctml:{start|end}>...</ctml:{start|end}>` block and
/// parses its single year/month/day precision child into a `PartialDate`.
fn extract_first_date(xml: &str, tag: &str) -> Option<PartialDate> {
    let block = extract_dated_element(xml, tag)?;
    for precision in ["day", "month", "year"] {
        let needle = format!("<ctml:{precision} ");
        if let Some(idx) = block.find(&needle) {
            let tag_end = block[idx..].find('/').map(|i| idx + i).unwrap_or(block.len());
            let tag_text = &block[idx..tag_end];
            let y: i32 = attr_value(tag_text, "y")?.parse().ok()?;
            let m: Option<u32> = attr_value(tag_text, "m").and_then(|s| s.parse().ok());
            let d: Option<u32> = attr_value(tag_text, "d").and_then(|s| s.parse().ok());
            return Some(PartialDate { y, m, d });
        }
    }
    None
}

fn extract_dated_element(xml: &str, tag: &str) -> Option<String> {
    let open = format!("<ctml:{tag}>");
    let close = format!("</ctml:{tag}>");
    let start = xml.find(&open)?;
    let end = xml[start..].find(&close)? + start + close.len();
    Some(xml[start..end].to_string())
}

fn replace_dated_element(xml: &str, tag: &str, new_block: &str) -> String {
    let open = format!("<ctml:{tag}>");
    let close = format!("</ctml:{tag}>");
    let Some(start) = xml.find(&open) else { return xml.to_string() };
    let Some(end_rel) = xml[start..].find(&close) else { return xml.to_string() };
    let end = start + end_rel + close.len();
    format!("{}{}{}", &xml[..start], new_block, &xml[end..])
}

/// Finds the first `<ctml:{tag_local} .../>`-shaped element and returns
/// one of its attributes. Safe to use where there's only ever one such
/// element in scope (e.g. one `<ctml:playerRef>` per participant block)
/// — for anything where document order doesn't uniquely identify the
/// right element (e.g. which of two `<ctml:fingerprint>` elements),
/// see [`extract_trajectory_value`] instead of this.
fn extract_first_attr(xml: &str, tag_local: &str, attr: &str) -> Option<String> {
    let needle = format!("<ctml:{tag_local} ");
    let start = xml.find(&needle)?;
    let tag_end = xml[start..].find('>').map(|i| start + i).unwrap_or(xml.len());
    attr_value(&xml[start..tag_end], attr)
}

/// Finds the `<ctml:fingerprint scope="trajectory" .../>` element
/// specifically (checked by `scope`, not by which `<ctml:fingerprint>`
/// comes first in the text) and returns its `value`. There are always
/// two `<ctml:fingerprint>` elements per game (trajectory,
/// finalPosition); relying on document order to tell them apart would
/// silently break if that emission order ever changed.
fn extract_trajectory_value(xml: &str) -> Option<String> {
    let mut pos = 0;
    loop {
        let needle = "<ctml:fingerprint ";
        let rel = xml[pos..].find(needle)?;
        let start = pos + rel;
        let tag_end = xml[start..].find('/').map(|i| start + i).unwrap_or(xml.len());
        let tag_text = &xml[start..tag_end];
        if attr_value(tag_text, "scope").as_deref() == Some("trajectory") {
            return attr_value(tag_text, "value");
        }
        pos = tag_end;
    }
}

fn extract_fingerprints_block(game_block: &str) -> Option<String> {
    let open = "<ctml:fingerprints>";
    let close = "</ctml:fingerprints>";
    let start = game_block.find(open)?;
    let end = game_block[start..].find(close)? + start + close.len();
    Some(game_block[start..end].to_string())
}

fn splice_fingerprints_into_existing_game(existing_text: &str, round: &str, white_id: &str, black_id: &str, fp_block: &str) -> String {
    // Located by the existing file's own (white_id, black_id, round) —
    // round alone is never unique within a tournament (every board in a
    // round shares it). A conservative fallback: if the exact slot can't
    // be found, leave the file untouched rather than risk corrupting it
    // — enrichment is a nice-to-have, correctness of the rest of the
    // merge is not negotiable.
    let marker = format!(r#"round="{round}" white="{white_id}" black="{black_id}""#);
    let Some(game_start) = existing_text.find(&marker) else { return existing_text.to_string() };
    let Some(game_end_rel) = existing_text[game_start..].find("</ctml:game>") else { return existing_text.to_string() };
    let game_end = game_start + game_end_rel;
    let game_block = &existing_text[game_start..game_end];
    if game_block.contains("<ctml:fingerprints>") {
        return existing_text.to_string(); // already has one somehow; don't duplicate
    }
    let insert_at = existing_text[game_start..game_end]
        .find("<ctml:source")
        .map(|i| game_start + i)
        .unwrap_or(game_end);
    format!("{}      {}\n{}", &existing_text[..insert_at], fp_block, &existing_text[insert_at..])
}
