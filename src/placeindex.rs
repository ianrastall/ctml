//! In-memory name-lookup index over the CTML place registry's city
//! shards — a direct port of
//! `D:\dev\proj\ctml\readers\place_registry_index.py`. Only
//! `places-cities-*.xml` shards are indexed (tournament venues are
//! cities, not countries/admin1), and matching is plain normalized-name
//! equality; a `Site` string matching more than one distinct city (e.g.
//! `"Paris"` — France and several in the US) is left unresolved rather
//! than guessed, same policy as the player resolver.

use quick_xml::events::{BytesStart, Event};
use quick_xml::Reader;
use std::collections::HashMap;
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

fn normalize_place_key(raw: &str) -> String {
    let lower = raw.trim().to_lowercase();
    lower.split_whitespace().collect::<Vec<_>>().join(" ")
}

#[derive(Default)]
pub struct PlaceIndex {
    by_name: HashMap<String, Vec<String>>,
    pub place_count: u64,
}

impl PlaceIndex {
    fn add(&mut self, ref_: &str, name: &str) {
        let key = normalize_place_key(name);
        let list = self.by_name.entry(key).or_default();
        if !list.iter().any(|r| r == ref_) {
            list.push(ref_.to_string());
        }
    }

    pub fn resolve(&self, raw_site: &str) -> (Option<String>, &'static str) {
        let key = normalize_place_key(raw_site);
        if let Some(cands) = self.by_name.get(&key) {
            if cands.len() == 1 {
                return (Some(cands[0].clone()), "name-exact");
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

fn scan_one_shard(path: &Path, idx: &mut PlaceIndex) -> std::io::Result<()> {
    let file = File::open(path)?;
    let buffered = BufReader::with_capacity(1 << 20, file);
    let mut xml = Reader::from_reader(buffered);
    xml.config_mut().trim_text(true);
    let mut buf = Vec::with_capacity(1 << 16);

    let mut in_place = false;
    let mut in_name = false;
    let mut cur_ref = String::new();

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let local = e.local_name();
                if local.as_ref() == b"place" {
                    in_place = true;
                    cur_ref = attr(&e, b"ref").unwrap_or_default();
                } else if in_place && local.as_ref() == b"name" {
                    in_name = true;
                }
            }
            Ok(Event::End(e)) => {
                let local = e.local_name();
                if local.as_ref() == b"place" {
                    in_place = false;
                } else if local.as_ref() == b"name" {
                    in_name = false;
                }
            }
            Ok(Event::Text(e)) => {
                if in_place && in_name {
                    if let Ok(text) = e.unescape() {
                        let t = text.trim();
                        if !t.is_empty() && !cur_ref.is_empty() {
                            idx.add(&cur_ref, t);
                            idx.place_count += 1;
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

pub fn build_index(places_dir: &Path) -> std::io::Result<PlaceIndex> {
    let mut files: Vec<_> = std::fs::read_dir(places_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            p.file_name()
                .and_then(|n| n.to_str())
                .is_some_and(|n| n.starts_with("places-cities-") && n.ends_with(".xml"))
        })
        .collect();
    files.sort();

    let mut idx = PlaceIndex::default();
    for file in &files {
        scan_one_shard(file, &mut idx)?;
    }
    Ok(idx)
}
