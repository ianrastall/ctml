//! In-memory lookup index over `assets/registries/events.xml` — a direct
//! port of `D:\dev\proj\ctml\readers\event_registry_index.py`. Small
//! enough (10,433 occurrences) to load as one pass, unlike the player
//! registry. Three-tier match, cheapest first, read from the reference
//! rather than reinvented:
//!
//! 1. **exact-ref**: the importer synthesizes
//!    `event:<start.compact()>-<end.compact()>-<slug(name)>` for every
//!    tournament using the same convention the registry itself was
//!    seeded with; if that computed ref is already a registry key, it's
//!    an exact match with no fuzzy logic at all.
//! 2. **name-date-overlap**: same slug, and the incoming date range
//!    overlaps the candidate occurrence's — covers a different source
//!    spelling the event the same way but with different date precision.
//! 3. **slug-prefix-date-overlap**: the incoming slug is a prefix of a
//!    candidate's slug (on a `-` boundary), still gated by date overlap
//!    — covers e.g. TWIC's `"Dortmund GER (GER), 9-17 vii 1999"` against
//!    Mega Database's bare `"Dortmund"` for the same event.

use crate::tournament::PartialDate;
use crate::xmlutil::slug;
use quick_xml::events::{BytesStart, Event};
use quick_xml::Reader;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::BufReader;
use std::path::Path;

#[derive(Default)]
pub struct EventIndex {
    refs: HashSet<String>,
    by_name: HashMap<String, Vec<(String, i64, i64)>>, // slug(name) -> (ref, start_day, end_day)
    pub occurrence_count: u64,
}

impl EventIndex {
    /// `(matched_ref_or_none, method)`.
    pub fn resolve(&self, name: &str, start: &PartialDate, end: &PartialDate) -> (Option<String>, &'static str) {
        let candidate_ref = format!("event:{}-{}-{}", start.compact(), end.compact(), slug(name, "unknown"));
        if self.refs.contains(&candidate_ref) {
            return (Some(candidate_ref), "exact-ref");
        }

        let key = slug(name, "unknown");
        let (approx_start, approx_end) = (start.approx_epoch_day(), end.approx_epoch_day());

        if let Some(entries) = self.by_name.get(&key) {
            for (ref_, o_start, o_end) in entries {
                if approx_start <= *o_end && *o_start <= approx_end {
                    return (Some(ref_.clone()), "name-date-overlap");
                }
            }
        }

        let prefix = format!("{key}-");
        for (cand_key, entries) in &self.by_name {
            if *cand_key != key && !cand_key.starts_with(&prefix) {
                continue;
            }
            for (ref_, o_start, o_end) in entries {
                if approx_start <= *o_end && *o_start <= approx_end {
                    return (Some(ref_.clone()), "slug-prefix-date-overlap");
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
    Occurrence,
    Name,
    Aliases,
    Alias,
    Start,
    End,
    Year,
    Month,
    Day,
    Other,
}

fn tag_of(local: &[u8]) -> Tag {
    match local {
        b"eventOccurrence" => Tag::Occurrence,
        b"name" => Tag::Name,
        b"aliases" => Tag::Aliases,
        b"alias" => Tag::Alias,
        b"start" => Tag::Start,
        b"end" => Tag::End,
        b"year" => Tag::Year,
        b"month" => Tag::Month,
        b"day" => Tag::Day,
        _ => Tag::Other,
    }
}

fn parse_date_attrs(e: &BytesStart) -> Option<PartialDate> {
    let y: i32 = attr(e, b"y")?.parse().ok()?;
    let m: Option<u32> = attr(e, b"m").and_then(|s| s.parse().ok());
    let d: Option<u32> = attr(e, b"d").and_then(|s| s.parse().ok());
    Some(PartialDate { y, m, d })
}

pub fn build_index(events_path: &Path) -> std::io::Result<EventIndex> {
    let file = File::open(events_path)?;
    let buffered = BufReader::with_capacity(1 << 20, file);
    let mut xml = Reader::from_reader(buffered);
    xml.config_mut().trim_text(true);
    let mut buf = Vec::with_capacity(1 << 16);

    let mut idx = EventIndex::default();
    let mut stack: Vec<Tag> = Vec::with_capacity(8);

    let mut cur_ref = String::new();
    let mut cur_names: Vec<String> = Vec::new();
    let mut cur_start: Option<PartialDate> = None;
    let mut cur_end: Option<PartialDate> = None;
    let mut in_start = false;
    let mut in_end = false;

    loop {
        match xml.read_event_into(&mut buf) {
            Ok(Event::Eof) => break,
            Ok(Event::Start(e)) => {
                let tag = tag_of(e.local_name().as_ref());
                match tag {
                    Tag::Occurrence => {
                        cur_ref = attr(&e, b"ref").unwrap_or_default();
                        cur_names.clear();
                        cur_start = None;
                        cur_end = None;
                    }
                    Tag::Start => in_start = true,
                    Tag::End => in_end = true,
                    _ => {}
                }
                stack.push(tag);
            }
            Ok(Event::Empty(e)) => {
                let tag = tag_of(e.local_name().as_ref());
                if matches!(tag, Tag::Year | Tag::Month | Tag::Day) {
                    if let Some(pd) = parse_date_attrs(&e) {
                        if in_start && cur_start.is_none() {
                            cur_start = Some(pd);
                        } else if in_end && cur_end.is_none() {
                            cur_end = Some(pd);
                        }
                    }
                }
            }
            Ok(Event::End(_)) => match stack.pop() {
                Some(Tag::Occurrence) => {
                    idx.refs.insert(cur_ref.clone());
                    idx.occurrence_count += 1;
                    if let (Some(start), Some(end)) = (&cur_start, &cur_end) {
                        let (sd, ed) = (start.approx_epoch_day(), end.approx_epoch_day());
                        for name in &cur_names {
                            idx.by_name.entry(slug(name, "unknown")).or_default().push((cur_ref.clone(), sd, ed));
                        }
                    }
                }
                Some(Tag::Start) => in_start = false,
                Some(Tag::End) => in_end = false,
                _ => {}
            },
            Ok(Event::Text(e)) => {
                let top = stack.last().copied();
                let parent = if stack.len() >= 2 { Some(stack[stack.len() - 2]) } else { None };
                if let Ok(text) = e.unescape() {
                    let t = text.trim();
                    if !t.is_empty() {
                        match (top, parent) {
                            (Some(Tag::Name), Some(Tag::Occurrence)) => cur_names.push(t.to_string()),
                            (Some(Tag::Alias), Some(Tag::Aliases)) => cur_names.push(t.to_string()),
                            _ => {}
                        }
                    }
                }
            }
            Ok(_) => {}
            Err(err) => {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    format!("{}: {err}", events_path.display()),
                ));
            }
        }
        buf.clear();
    }

    Ok(idx)
}
