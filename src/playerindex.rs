//! In-memory name-lookup index over the sharded CTML player registry —
//! a direct port of `D:\dev\proj\ctml\readers\player_registry_index.py`.
//! Resolution cascade (name-exact, then surname-unique for a bare
//! single-token query) is read from that file, not reinvented: no
//! fuzzy/edit-distance matching anywhere — an ambiguous or unmatched
//! name is meant to surface as a registry candidate for later curation,
//! never guessed at import time.
//!
//! Deliberately lighter than [`crate::xmlplayers`]: that module builds a
//! full typed record (ratings, aliases as structured names) keyed by
//! FIDE id only, because it exists to diff against the SSP. This module
//! only needs `(ref, display names, family)` and needs it for *every*
//! player, FIDE-keyed or not — a PGN's White/Black name has to resolve
//! against the ~88,000 non-FIDE players too.

use quick_xml::events::{BytesStart, Event};
use quick_xml::Reader;
use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

fn normalize_name_key(raw: &str) -> String {
    let lower = raw.trim().to_lowercase();
    let collapsed = lower.split_whitespace().collect::<Vec<_>>().join(" ");
    collapsed.replace('.', "")
}

fn surname_key(raw: &str) -> Option<String> {
    let text = raw.trim();
    if text.contains(',') || text.contains(' ') {
        None
    } else {
        Some(normalize_name_key(text))
    }
}

#[derive(Default)]
pub struct PlayerIndex {
    by_name: HashMap<String, Vec<String>>,
    by_surname: HashMap<String, Vec<String>>,
    pub player_count: u64,
}

impl PlayerIndex {
    fn add_name(&mut self, key: String, ref_: &str) {
        let list = self.by_name.entry(key).or_default();
        if !list.iter().any(|r| r == ref_) {
            list.push(ref_.to_string());
        }
    }

    fn add(&mut self, ref_: &str, display_names: &[String], family: Option<&str>) {
        for name in display_names {
            if !name.is_empty() {
                self.add_name(normalize_name_key(name), ref_);
            }
        }
        if let Some(fam) = family {
            let key = normalize_name_key(fam);
            let list = self.by_surname.entry(key).or_default();
            if !list.iter().any(|r| r == ref_) {
                list.push(ref_.to_string());
            }
        }
    }

    /// `(ref_or_none, ResolutionMethodType value)`.
    pub fn resolve(&self, raw_name: &str) -> (Option<String>, &'static str) {
        let key = normalize_name_key(raw_name);
        if let Some(cands) = self.by_name.get(&key) {
            if cands.len() == 1 {
                return (Some(cands[0].clone()), "name-exact");
            }
        }
        if let Some(sk) = surname_key(raw_name) {
            if let Some(cands) = self.by_surname.get(&sk) {
                if cands.len() == 1 {
                    return (Some(cands[0].clone()), "surname-unique");
                }
            }
        }
        (None, "unresolved")
    }
}

fn attr(e: &BytesStart, key: &[u8]) -> Option<String> {
    e.attributes()
        .flatten()
        .find(|a| a.key.as_ref() == key)
        .and_then(|a| a.unescape_value().ok())
        .map(|c| c.into_owned())
}

#[derive(Clone, Copy, PartialEq)]
enum Tag {
    Player,
    Name,
    Family,
    Aliases,
    Alias,
    Other,
}

fn tag_of(local: &[u8]) -> Tag {
    match local {
        b"player" => Tag::Player,
        b"name" => Tag::Name,
        b"family" => Tag::Family,
        b"aliases" => Tag::Aliases,
        b"alias" => Tag::Alias,
        _ => Tag::Other,
    }
}

fn scan_one_shard(path: &Path, idx: &mut PlayerIndex) -> std::io::Result<()> {
    let file = File::open(path)?;
    let buffered = BufReader::with_capacity(1 << 20, file);
    let mut xml = Reader::from_reader(buffered);
    xml.config_mut().trim_text(true);
    let mut buf = Vec::with_capacity(1 << 16);

    let mut stack: Vec<Tag> = Vec::with_capacity(8);
    let mut cur_ref = String::new();
    let mut names: Vec<String> = Vec::new();
    let mut family: Option<String> = None;

    macro_rules! enter {
        ($tag:expr, $e:expr) => {{
            let tag = $tag;
            let parent = stack.last().copied();
            match tag {
                Tag::Player => {
                    cur_ref = attr($e, b"ref").unwrap_or_default();
                    names.clear();
                    family = None;
                }
                Tag::Name if parent == Some(Tag::Player) => {
                    if let Some(d) = attr($e, b"display") {
                        names.push(d);
                    }
                }
                Tag::Alias if parent == Some(Tag::Aliases) => {
                    if let Some(d) = attr($e, b"display") {
                        names.push(d);
                    }
                }
                _ => {}
            }
            stack.push(tag);
        }};
    }

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let tag = tag_of(e.local_name().as_ref());
                enter!(tag, &e);
            }
            Ok(Event::Empty(e)) => {
                let tag = tag_of(e.local_name().as_ref());
                enter!(tag, &e);
                if stack.pop() == Some(Tag::Player) {
                    idx.add(&cur_ref, &names, family.as_deref());
                    idx.player_count += 1;
                }
            }
            Ok(Event::End(_)) => {
                if stack.pop() == Some(Tag::Player) {
                    idx.add(&cur_ref, &names, family.as_deref());
                    idx.player_count += 1;
                }
            }
            Ok(Event::Text(e)) => {
                if stack.last() == Some(&Tag::Family)
                    && stack.len() >= 2
                    && stack[stack.len() - 2] == Tag::Name
                {
                    if let Ok(text) = e.unescape() {
                        let t = text.trim();
                        if !t.is_empty() {
                            family = Some(t.to_string());
                        }
                    }
                }
            }
            Ok(_) => {}
            Err(err) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("{}: {err}", path.display()),
                ));
            }
        }
        buf.clear();
    }

    Ok(())
}

pub fn build_index(players_dir: &Path) -> std::io::Result<PlayerIndex> {
    let mut files: Vec<_> = std::fs::read_dir(players_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|s| s.to_str()) == Some("xml"))
        .collect();
    files.sort();

    let mut idx = PlayerIndex::default();
    for file in &files {
        scan_one_shard(file, &mut idx)?;
    }
    Ok(idx)
}
