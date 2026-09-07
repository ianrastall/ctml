//! Groups parsed PGN games into tournament occurrences — a direct port
//! of `scripts/pgn_to_ctml.py::group_tournaments`. PGN databases (a Mega
//! Database export, say) interleave games from thousands of different
//! events in no particular order. Games are grouped by normalized
//! `(Event, Site)`, then within each such group, split into separate
//! occurrences wherever there's a date gap of more than `max_gap_days`
//! (default 21 in the reference) between consecutive games sorted by
//! date — otherwise two different years of an annual "City Open" would
//! merge into one tournament spanning a year. 21 days is generous enough
//! to keep a single real event together (byes, split sessions) while
//! safely separating distinct editions of a series, which are almost
//! always months apart.

use crate::gameimport::ParsedGame;
use crate::tournament::PartialDate;
use crate::xmlutil::slug;
use std::collections::HashMap;

pub struct Tournament {
    pub event: String,
    pub site: String,
    pub start: PartialDate,
    pub end: PartialDate,
    /// No game in this occurrence had a parseable date at all —
    /// `start`/`end` are the `1970-01-01` placeholder the reference
    /// script uses in that case, not a real date.
    pub dates_unknown: bool,
    pub games: Vec<ParsedGame>,
}

pub fn group_tournaments(games: Vec<ParsedGame>, max_gap_days: i64) -> Vec<Tournament> {
    let mut by_key: HashMap<(String, String), Vec<ParsedGame>> = HashMap::new();
    for g in games {
        let key = (slug(&g.event, "unknown"), slug(&g.site, "unknown"));
        by_key.entry(key).or_default().push(g);
    }

    let mut tournaments = Vec::new();
    for (_, group) in by_key {
        let (mut dated, mut undated): (Vec<ParsedGame>, Vec<ParsedGame>) =
            group.into_iter().partition(|g| g.date.is_some());
        dated.sort_by_key(|g| g.date.as_ref().unwrap().approx_epoch_day());

        let mut runs: Vec<Vec<ParsedGame>> = Vec::new();
        for g in dated {
            let g_day = g.date.as_ref().unwrap().approx_epoch_day();
            let attach = runs.last().is_some_and(|run: &Vec<ParsedGame>| {
                let last_day = run.last().unwrap().date.as_ref().unwrap().approx_epoch_day();
                g_day - last_day <= max_gap_days
            });
            if attach {
                runs.last_mut().unwrap().push(g);
            } else {
                runs.push(vec![g]);
            }
        }

        if !undated.is_empty() {
            // No date to anchor on: attach to the single existing run if
            // there is exactly one, otherwise keep as its own group (no
            // way to safely decide which occurrence they belong to).
            match runs.len() {
                1 => runs[0].extend(undated.drain(..)),
                0 => runs.push(undated.drain(..).collect()),
                _ => runs.push(undated.drain(..).collect()),
            }
        }

        for run in runs {
            let dates: Vec<&PartialDate> = run.iter().filter_map(|g| g.date.as_ref()).collect();
            let (start, end, dates_unknown) = if !dates.is_empty() {
                let start = dates.iter().min_by_key(|d| d.approx_epoch_day()).unwrap();
                let end = dates.iter().max_by_key(|d| d.approx_epoch_day()).unwrap();
                ((*start).clone(), (*end).clone(), false)
            } else {
                // Matches the reference's own placeholder convention
                // (system-overview.md) rather than silently dropping
                // games with no date anywhere in their group.
                (PartialDate { y: 1970, m: Some(1), d: Some(1) }, PartialDate { y: 1970, m: Some(1), d: Some(1) }, true)
            };
            let event = run[0].event.clone();
            let site = run[0].site.clone();
            tournaments.push(Tournament { event, site, start, end, dates_unknown, games: run });
        }
    }

    tournaments
}
