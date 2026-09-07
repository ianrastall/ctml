//! Renders a grouped [`crate::tournamentgroup::Tournament`] as
//! `ctml:tournament` XML — a direct port of
//! `scripts/pgn_to_ctml.py::tournament_xml` (participant dedup/ordering,
//! registry resolution stats, element shape and order all read from
//! there, not redesigned).

use crate::eventindex::EventIndex;
use crate::gameimport;
use crate::placeindex::PlaceIndex;
use crate::playerindex::PlayerIndex;
use crate::tournamentgroup::Tournament;
use crate::xmlutil::{esc, normalize_space, sha1_hex16, slug};
use std::collections::HashMap;

#[derive(Default)]
pub struct ResolutionStats {
    pub events_total: u64,
    pub events_resolved: u64,
    pub places_total: u64,
    pub places_resolved: u64,
    pub participants_total: u64,
    pub participants_resolved: u64,
}

fn synth_player_ref(name: &str) -> String {
    format!("player:syn:{}", sha1_hex16(&name.trim().to_lowercase()))
}

fn place_raw_ref(site: &str) -> String {
    format!("place:raw:{}", sha1_hex16(&site.trim().to_lowercase()))
}

/// `pgn_to_ctml.py::person_name_xml` — deliberately **not**
/// `names::split_name`: this converter's own reference function never
/// pops a `Jr.`/`Sr.`/roman-numeral suffix the way the SSP and
/// crosstable converters do. That's a real difference between the two
/// Python converters, not an oversight to "fix" by unifying them here.
fn person_name_xml(raw: &str, indent: &str, tag: &str) -> String {
    let n = normalize_space(raw);
    let raw = if n.is_empty() { "Unknown".to_string() } else { n };
    let mut lines = vec![format!(r#"{indent}<ctml:{tag} display="{}">"#, esc(&raw))];
    if let Some(idx) = raw.find(',') {
        let family = raw[..idx].trim();
        let rest = raw[idx + 1..].trim();
        lines.push(format!("{indent}  <ctml:family>{}</ctml:family>", esc(family)));
        for given in rest.split_whitespace() {
            lines.push(format!("{indent}  <ctml:given>{}</ctml:given>", esc(given)));
        }
    } else {
        let tokens: Vec<&str> = raw.split_whitespace().collect();
        if tokens.len() <= 1 {
            lines.push(format!("{indent}  <ctml:family>{}</ctml:family>", esc(&raw)));
        } else {
            lines.push(format!("{indent}  <ctml:family>{}</ctml:family>", esc(tokens[tokens.len() - 1])));
            for given in &tokens[..tokens.len() - 1] {
                lines.push(format!("{indent}  <ctml:given>{}</ctml:given>", esc(given)));
            }
        }
    }
    lines.push(format!("{indent}</ctml:{tag}>"));
    lines.join("\n")
}

/// Returns `(xml, event_ref, event_was_resolved)` — the caller needs
/// `event_ref` to pick a corpus file and `event_was_resolved` to decide
/// whether this tournament is a `--register-new-events`-style candidate
/// (that flag itself isn't implemented yet — see `HANDOFF.md`).
pub fn tournament_xml(
    t: &Tournament,
    tid: &str,
    player_index: Option<&PlayerIndex>,
    event_index: Option<&EventIndex>,
    place_index: Option<&PlaceIndex>,
    stats: &mut ResolutionStats,
    source_label: &str,
) -> (String, String, bool) {
    let (mut event_ref, _event_method) = match event_index {
        Some(idx) => {
            let (r, m) = idx.resolve(&t.event, &t.start, &t.end);
            (r, m)
        }
        None => (None, "unresolved"),
    };
    stats.events_total += 1;
    let event_was_resolved = event_ref.is_some();
    if event_ref.is_none() {
        event_ref = Some(format!("event:{}-{}-{}", t.start.compact(), t.end.compact(), slug(&t.event, "unknown")));
    } else {
        stats.events_resolved += 1;
    }
    let event_ref = event_ref.unwrap();

    let mut lines = vec![
        r#"<?xml version="1.0" encoding="UTF-8"?>"#.to_string(),
        format!(r#"<ctml:tournament xmlns:ctml="urn:ctml:2.0" ctmlVersion="2.0" id="{}">"#, esc(tid)),
        "  <ctml:header>".to_string(),
        format!("    <ctml:name>{}</ctml:name>", esc(&t.event)),
        format!(r#"    <ctml:eventRef ref="{}"><ctml:name>{}</ctml:name></ctml:eventRef>"#, esc(&event_ref), esc(&t.event)),
        "    <ctml:dates>".to_string(),
        format!("      {}", t.start.element("start")),
        format!("      {}", t.end.element("end")),
        "    </ctml:dates>".to_string(),
    ];

    if !t.site.is_empty() {
        let (place_ref, place_method) = match place_index {
            Some(idx) => idx.resolve(&t.site),
            None => (None, "unresolved"),
        };
        let _ = place_method;
        stats.places_total += 1;
        let place_ref = match place_ref {
            Some(r) => {
                stats.places_resolved += 1;
                r
            }
            None => place_raw_ref(&t.site),
        };
        lines.push(format!(
            r#"    <ctml:placeRef ref="{}"><ctml:name>{}</ctml:name></ctml:placeRef>"#,
            esc(&place_ref),
            esc(&t.site)
        ));
    }
    lines.push("  </ctml:header>".to_string());

    // Participants: one per distinct name appearing as White/Black across
    // this tournament's games, in first-seen order.
    let mut participant_id: HashMap<String, String> = HashMap::new();
    let mut order: Vec<String> = Vec::new();
    for g in &t.games {
        for name in [&g.white, &g.black] {
            if !name.is_empty() && !participant_id.contains_key(name) {
                let pid = format!("p{:04}", participant_id.len() + 1);
                participant_id.insert(name.clone(), pid);
                order.push(name.clone());
            }
        }
    }

    lines.push("  <ctml:participants>".to_string());
    for name in &order {
        let pid = &participant_id[name];
        let (ref_, method) = match player_index {
            Some(idx) => idx.resolve(name),
            None => (None, "unresolved"),
        };
        stats.participants_total += 1;
        let (ref_, method) = match ref_ {
            Some(r) => {
                stats.participants_resolved += 1;
                (r, method)
            }
            None => (synth_player_ref(name), "unresolved"),
        };
        lines.push(format!(r#"    <ctml:participant id="{pid}">"#));
        lines.push(format!(r#"      <ctml:playerRef ref="{}">"#, esc(&ref_)));
        lines.push(person_name_xml(name, "        ", "name"));
        lines.push(format!(r#"        <ctml:resolution method="{method}"/>"#));
        lines.push("      </ctml:playerRef>".to_string());
        lines.push("    </ctml:participant>".to_string());
    }
    lines.push("  </ctml:participants>".to_string());

    lines.push("  <ctml:games>".to_string());
    for g in &t.games {
        let white_id = participant_id.get(&g.white);
        let black_id = participant_id.get(&g.black);
        let (Some(white_id), Some(black_id)) = (white_id, black_id) else { continue };
        if white_id == black_id {
            continue; // degenerate (e.g. both names empty and equal) — skip rather than emit invalid data
        }
        lines.push(gameimport::game_xml(g, white_id, black_id, source_label, "    "));
    }
    lines.push("  </ctml:games>".to_string());

    if t.dates_unknown {
        lines.push(
            "  <ctml:notes>Source PGN had no parseable date for any game in this group; 1970-01-01 is a placeholder, not a real date.</ctml:notes>"
                .to_string(),
        );
    }
    lines.push(format!(r#"  <ctml:source kind="{}"/>"#, esc(source_label)));
    lines.push("</ctml:tournament>".to_string());
    lines.push(String::new());

    (lines.join("\n"), event_ref, event_was_resolved)
}
