"use strict";
/* CTML viewer / maker / exporter — pure client-side. */
(function () {
const NS = "urn:ctml:2.0";
const PIECE = { p: "♟", n: "♞", b: "♝", r: "♜", q: "♛", k: "♚" };

/* ---------- tiny DOM + XML helpers ---------- */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const kids = (el, name) => el ? [...el.children].filter((c) => c.localName === name) : [];
const kid = (el, name) => kids(el, name)[0] || null;
const txt = (el, name) => { const k = kid(el, name); return k ? k.textContent.trim() : null; };
const attr = (el, a) => (el && el.getAttribute(a)) || null;
function h(tag, props, ...children) {
  const e = document.createElement(tag);
  if (props) for (const [k, v] of Object.entries(props)) {
    if (v == null) continue;
    if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const c of children.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}

/* ---------- state ---------- */
let RAW = "", DOC = null, ROOT = null, NAMEBYID = {}, FILENAME = "tournament.ctml";

/* ---------- init ---------- */
document.addEventListener("DOMContentLoaded", () => {
  wireTabs(); wireTheme(); wireLoader(); wireMaker();
  loadCorpus();
  route();
  window.addEventListener("hashchange", route);
});

function route() {
  const v = (location.hash.replace("#", "") || "viewer");
  $$(".view").forEach((s) => (s.hidden = s.id !== "view-" + v));
  $$("#tabs a").forEach((a) => a.classList.toggle("active", a.dataset.view === v));
}
function wireTabs() { /* links use hash; route() handles it */ }
function wireTheme() {
  const root = document.documentElement, btn = $("#themeToggle");
  btn.addEventListener("click", () => {
    const cur = root.getAttribute("data-theme");
    const next = cur === "dark" ? "light" : cur === "light" ? "" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("ctml-theme", next); } catch (e) {}
  });
  try { const s = localStorage.getItem("ctml-theme"); if (s != null) root.setAttribute("data-theme", s); } catch (e) {}
}

/* ---------- corpus picker ---------- */
async function loadCorpus() {
  const sel = $("#corpusPicker");
  try {
    const list = await (await fetch("corpus.json", { cache: "no-cache" })).json();
    sel.innerHTML = '<option value="">— choose a tournament —</option>';
    for (const e of list) {
      const yr = (e.start || "").slice(0, 4);
      sel.append(h("option", { value: e.file }, `${yr}  ·  ${e.name}`));
    }
    sel.addEventListener("change", () => { if (sel.value) loadUrl(sel.value); });
    renderCorpusTable(list);
  } catch (err) {
    sel.innerHTML = '<option value="">(corpus.json not found — open a file instead)</option>';
  }
}

/* Browsable corpus table: one row per tournament, headline field-strength
   figure (fide/standard when present, else the first fide entry), row shaded
   by FIDE category — light accent from category 15, darker accent from
   category 20 (same colour family, tightening as strength rises). */
function renderCorpusTable(list) {
  const host = $("#corpusTable"); if (!host) return;
  const catClass = (n) => (!n ? "" : n >= 20 ? "cat-vhigh" : n >= 15 ? "cat-high" : "");
  const rows = list.map((e) => {
    const yr = (e.start || "").slice(0, 4);
    const types = (e.eventType || []).join(", ");
    const cadence = e.cadence || "";
    const cnt = e.participants || e.teams || 0;
    const avg = e.avgRating != null ? e.avgRating : "";
    const cat = e.fideCategory || "";
    const scopeTag = (() => {
      const s = (e.strength || []).find((x) => x.value === e.avgRating);
      return s && s.scope && s.scope !== "standard" ? h("span", { class: "scope-tag" }, s.scope) : null;
    })();
    return h("tr", { class: "click " + catClass(e.fideCategory), onclick: () => loadUrl(e.file) },
      h("td", { class: "mono num" }, yr),
      h("td", {}, e.name),
      h("td", { class: "mono t-type" }, types),
      h("td", { class: "mono t-cad" }, cadence),
      h("td", { class: "num" }, cnt || ""),
      h("td", { class: "num" }, avg, scopeTag),
      h("td", { class: "num t-cat" }, cat));
  });
  host.innerHTML = "";
  host.append(h("details", { class: "corpus-browse", open: true },
    h("summary", {}, `Browse the corpus (${list.length} tournaments)`),
    h("div", { class: "table-scroll" },
      h("table", { class: "data corpus" },
        h("thead", {}, h("tr", {},
          h("th", { class: "num" }, "Year"), h("th", {}, "Tournament"),
          h("th", {}, "Type"), h("th", {}, "Cadence"),
          h("th", { class: "num" }, "Players"),
          h("th", { class: "num" }, "Avg"),
          h("th", { class: "num" }, "Cat"))),
        h("tbody", {}, rows)))));
}
function wireLoader() {
  $("#fileInput").addEventListener("change", (e) => {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader();
    r.onload = () => loadText(r.result, f.name);
    r.readAsText(f);
  });
  $("#pasteToggle").addEventListener("click", () => { const p = $("#pastePane"); p.hidden = !p.hidden; });
  $("#pasteLoad").addEventListener("click", () => loadText($("#pasteArea").value, "pasted.ctml"));
  $("#exports").addEventListener("click", (e) => {
    const b = e.target.closest("[data-export]"); if (b) doExport(b.dataset.export);
  });
}
async function loadUrl(url) {
  try { const t = await (await fetch(url, { cache: "no-cache" })).text(); loadText(t, url.split("/").pop()); }
  catch (err) { $("#viewer").innerHTML = ""; $("#viewer").append(h("p", { class: "err" }, "Could not fetch " + url)); }
}
function loadText(text, filename) {
  RAW = text; FILENAME = (filename || "tournament.ctml").replace(/\.(xml)$/, ".ctml");
  const doc = new DOMParser().parseFromString(text, "application/xml");
  const perr = doc.querySelector("parsererror");
  if (perr || !doc.documentElement || doc.documentElement.localName !== "tournament") {
    $("#viewer").innerHTML = ""; $("#viewer").append(h("p", { class: "err" }, "Not a valid CTML tournament document."));
    $("#exports").hidden = true; return;
  }
  DOC = doc; ROOT = doc.documentElement; buildNameIndex(); render();
  $("#exports").hidden = false;
  location.hash = "viewer";
}

/* ---------- name index (participant/team id -> display) ---------- */
function buildNameIndex() {
  NAMEBYID = {};
  const parts = kid(ROOT, "participants");
  if (parts) for (const p of kids(parts, "participant")) {
    const ref = kid(p, "playerRef"); const nm = ref && kid(ref, "name");
    NAMEBYID[attr(p, "id")] = nm ? (attr(nm, "display") || nm.textContent.trim()) : attr(p, "id");
  }
  const teams = kid(ROOT, "teams");
  if (teams) for (const t of kids(teams, "team")) NAMEBYID[attr(t, "id")] = txt(t, "name") || attr(t, "id");
}
const nameOf = (id) => NAMEBYID[id] || id;

/* ---------- render ---------- */
function render() {
  const v = $("#viewer"); v.innerHTML = ""; v.scrollTop = 0;
  v.append(renderSummary());
  const teams = kid(ROOT, "teams");
  if (teams) v.append(renderTeams(teams));
  else v.append(renderParticipants());
  const groups = kid(ROOT, "groups");
  if (groups) v.append(renderGroups(groups));
  if (!teams) { const ct = renderCrosstable(); if (ct) v.append(ct); }
  const bracket = kid(ROOT, "bracket");
  if (bracket) v.append(renderBracket(bracket));
  v.append(renderGames());
  const notes = txt(ROOT, "notes"); if (notes) v.append(collapsible("Notes", h("div", { class: "prose" }, h("p", {}, notes))));
  const srcs = kids(ROOT, "source");
  if (srcs.length) v.append(renderSources(srcs));
}

function renderSummary() {
  const hdr = kid(ROOT, "header");
  const name = txt(hdr, "name") || "Tournament";
  const dates = kid(hdr, "dates");
  const d = dates ? `${dateStr(kid(dates, "start"))} – ${dateStr(kid(dates, "end"))}` : "";
  const place = kid(hdr, "placeRef");
  const placeStr = place ? [txt(place, "name"), txt(place, "country")].filter(Boolean).join(", ") : "";
  const chips = h("div", { class: "chips" });
  kids(hdr, "eventType").forEach((et) => chips.append(h("span", { class: "chip accent" }, et.textContent)));
  const cad = txt(hdr, "cadence"); if (cad) chips.append(h("span", { class: "chip" }, cad));
  const fed = txt(hdr, "federation"); if (fed) chips.append(h("span", { class: "chip" }, fed));
  // Field-strength: prefer stored averageRating (with FIDE category attr) on
  // the header; fall back to computing from participants' ratingSnapshots so
  // older tour files without the element still show a figure.
  const strengths = fieldStrength(hdr);
  for (const s of strengths) {
    const scopeTag = s.scope && s.scope !== "standard" ? " " + s.scope : "";
    const catTag = s.category ? " · Cat " + s.category : "";
    chips.append(h("span", { class: "chip " + (s.category >= 20 ? "cat-vhigh" : s.category >= 15 ? "cat-high" : ""),
                              title: s.derived ? "derived from participant ratings" : "stated on the tournament" },
      "Avg " + s.value + scopeTag + catTag));
  }
  const meta = h("div", { class: "meta" });
  if (d) meta.append(h("div", {}, h("b", {}, "Dates: "), d));
  if (placeStr) meta.append(h("div", {}, h("b", {}, "Location: "), [placeStr, txt(hdr, "venue")].filter(Boolean).join(" — ")));
  if (strengths.length) {
    const parts = strengths.map((s) => {
      const scopeTag = s.scope && s.scope !== "standard" ? " " + s.scope : "";
      return s.value + scopeTag + (s.category ? " (Cat " + s.category + ")" : "");
    });
    meta.append(h("div", {}, h("b", {}, "Average rating: "), parts.join(" · "),
      strengths.every((s) => s.derived) ? h("span", { class: "mono", style: "color:var(--ink-soft);font-size:.75rem" }, " (computed)") : null));
  }
  const orgs = kid(hdr, "organizers");
  if (orgs) meta.append(h("div", {}, h("b", {}, "Organizers: "), kids(orgs, "organizer").map((o) => txt(o, "name")).join("; ")));
  const arbs = kid(hdr, "arbiters");
  if (arbs) meta.append(h("div", {}, h("b", {}, "Arbiters: "), kids(arbs, "arbiter").map((a) => `${txt(a, "name")}${txt(a, "role") ? " (" + txt(a, "role") + ")" : ""}`).join("; ")));
  return h("div", { class: "tsummary" }, h("h1", {}, name), chips, meta);
}

/* Field-strength figures for the tournament: one entry per rating scope
   present. Prefers explicit header/averageRating elements (with their stated
   FIDE category); falls back to averaging participant ratingSnapshots of the
   same system+scope, deriving the category from the standard 25-point bands
   starting at 2251. Non-integer averages are rounded to the nearest integer
   the same way FIDE reports them. */
function fieldStrength(hdr) {
  const stated = kids(hdr, "averageRating").map((el) => {
    const v = parseInt(el.textContent, 10);
    if (!Number.isFinite(v)) return null;
    const catAttr = attr(el, "category");
    return { value: v, system: attr(el, "system") || "fide", scope: attr(el, "scope") || "standard",
             category: catAttr ? +catAttr : fideCategory(v), derived: false };
  }).filter(Boolean);
  if (stated.length) return stated;
  const parts = kid(ROOT, "participants");
  if (!parts) return [];
  const bins = new Map();
  for (const p of kids(parts, "participant")) {
    for (const r of kids(p, "ratingSnapshot")) {
      const val = parseInt(txt(r, "value") || "", 10);
      if (!Number.isFinite(val)) continue;
      const system = attr(r, "system") || "fide";
      const scope = attr(r, "scope") || "standard";
      const key = system + "|" + scope;
      let bin = bins.get(key);
      if (!bin) { bin = { system, scope, sum: 0, n: 0 }; bins.set(key, bin); }
      bin.sum += val; bin.n++;
    }
  }
  return [...bins.values()].map((b) => {
    const v = Math.round(b.sum / b.n);
    return { value: v, system: b.system, scope: b.scope, category: fideCategory(v), derived: true };
  });
}
function fideCategory(avg) { return avg >= 2251 ? Math.floor((avg - 2251) / 25) + 1 : 0; }

function renderParticipants() {
  const parts = kid(ROOT, "participants"); if (!parts) return h("div");
  const rows = kids(parts, "participant").map((p) => {
    const ref = kid(p, "playerRef"), nm = ref && kid(ref, "name");
    const rs = kids(p, "ratingSnapshot").map((r) => `${txt(r, "value")}${attr(r, "scope") && attr(r, "scope") !== "standard" ? " " + attr(r, "scope") : ""}`).join(" / ");
    return {
      placement: txt(p, "placement"), seed: txt(p, "seed"),
      name: nm ? (attr(nm, "display") || nm.textContent.trim()) : attr(p, "id"),
      title: ref ? kids(ref, "title").map((t) => t.textContent).join(" ") : "",
      fed: ref ? txt(ref, "federation") : "", rating: rs, score: txt(p, "score"),
    };
  });
  rows.sort((a, b) => (num(a.placement) - num(b.placement)) || (num(b.score) - num(a.score)) || (num(a.seed) - num(b.seed)));
  const hasPlace = rows.some((r) => r.placement), hasScore = rows.some((r) => r.score), hasRating = rows.some((r) => r.rating);
  const t = h("table", { class: "data" },
    h("thead", {}, h("tr", {},
      h("th", { class: "num" }, hasPlace ? "#" : "Seed"), h("th", {}, "Player"), h("th", {}, "Title"),
      h("th", {}, "Fed"), hasRating ? h("th", { class: "num" }, "Rating") : null, hasScore ? h("th", { class: "num" }, "Score") : null)),
    h("tbody", {}, rows.map((r, i) => {
      const rk = r.placement || (hasPlace ? "" : r.seed) || (i + 1);
      return h("tr", {},
        h("td", { class: "num rank " + (r.placement && +r.placement <= 3 ? "medal-" + r.placement : "") }, rk),
        h("td", {}, r.name), h("td", {}, r.title), h("td", {}, r.fed),
        hasRating ? h("td", { class: "num" }, r.rating) : null,
        hasScore ? h("td", { class: "num" }, r.score) : null);
    })));
  return panel("Standings", h("div", { class: "table-scroll" }, t));
}

function renderTeams(teams) {
  const rows = kids(teams, "team").map((t) => {
    const st = kid(t, "standing");
    const roster = kid(t, "roster");
    return {
      id: attr(t, "id"), name: txt(t, "name"),
      rank: st ? txt(st, "rank") : null, placement: st ? txt(st, "placement") : null,
      mp: st ? txt(st, "matchPoints") : null, gp: st ? txt(st, "gamePoints") : null,
      members: roster ? kids(roster, "member").map((m) => nameOf(attr(m, "participant"))) : [],
    };
  });
  rows.sort((a, b) => (num(a.placement || a.rank) - num(b.placement || b.rank)));
  const t = h("table", { class: "data" },
    h("thead", {}, h("tr", {}, h("th", { class: "num" }, "#"), h("th", {}, "Team"),
      h("th", { class: "num" }, "MP"), h("th", { class: "num" }, "Pts"), h("th", {}, "Roster"))),
    h("tbody", {}, rows.map((r) => h("tr", {},
      h("td", { class: "num rank " + (r.placement && +r.placement <= 3 ? "medal-" + r.placement : "") }, r.placement || r.rank || ""),
      h("td", {}, r.name),
      h("td", { class: "num" }, r.mp || ""), h("td", { class: "num" }, r.gp || ""),
      h("td", { class: "mono", style: "white-space:normal;font-size:.75rem;color:var(--ink-soft)" }, r.members.join(", "))))));
  return panel(`Teams (${rows.length})`, h("div", { class: "table-scroll" }, t));
}

function renderGroups(groups) {
  const body = h("div", { class: "body" });
  for (const g of kids(groups, "group")) {
    const members = kids(g, "member").map((m) => nameOf(attr(m, "competitor")));
    body.append(h("div", { style: "margin:.4rem 0" },
      h("b", {}, txt(g, "name") || attr(g, "id")),
      h("span", { class: "mono", style: "color:var(--ink-soft);font-size:.8rem" }, " — " + members.join(", "))));
  }
  return collapsible(`Groups (${kids(groups, "group").length})`, body, true);
}

function renderCrosstable() {
  const parts = kid(ROOT, "participants"); if (!parts) return null;
  const ps = kids(parts, "participant");
  if (ps.length < 3 || ps.length > 16) return null;
  const order = ps.map((p) => ({ id: attr(p, "id"), name: nameOf(attr(p, "id")), place: num(txt(p, "placement")), score: num(txt(p, "score")) }))
    .sort((a, b) => (a.place - b.place) || (b.score - a.score));
  const idx = {}; order.forEach((o, i) => (idx[o.id] = i));
  const cell = order.map(() => order.map(() => null));
  const gamesEl = kid(ROOT, "games"); if (!gamesEl) return null;
  let played = 0;
  for (const g of kids(gamesEl, "game")) {
    const w = attr(g, "white"), b = attr(g, "black"), r = attr(g, "result");
    if (!(w in idx) || !(b in idx)) continue;
    const [ws, bs] = scoreOf(r);
    if (ws == null) continue;
    played++;
    cell[idx[w]][idx[b]] = (cell[idx[w]][idx[b]] || 0) + ws;
    cell[idx[b]][idx[w]] = (cell[idx[b]][idx[w]] || 0) + bs;
  }
  if (!played) return null;
  const head = h("tr", {}, h("th", {}, ""), h("th", { class: "name" }, "Player"),
    ...order.map((_, i) => h("th", {}, i + 1)), h("th", {}, "Σ"));
  const body = order.map((o, i) => {
    let sum = 0;
    const tds = order.map((_, j) => {
      if (i === j) return h("td", { class: "self" }, "");
      const val = cell[i][j];
      if (val == null) return h("td", {}, "·");
      sum += val;
      const cls = val === 1 ? "w" : val === 0 ? "l" : "";
      return h("td", { class: cls }, fmt(val));
    });
    return h("tr", {}, h("td", { class: "rank" }, i + 1), h("td", { class: "name" }, o.name), ...tds, h("td", { class: "mono" }, fmt(sum)));
  });
  return panel("Crosstable", h("div", { class: "table-scroll" }, h("table", { class: "crosstable" }, h("thead", {}, head), h("tbody", {}, body))));
}

function renderBracket(bracket) {
  const body = h("div", { class: "body" });
  for (const st of kids(bracket, "stage")) {
    const wrap = h("div", { class: "bracket-stage" }, h("h3", {}, (attr(st, "name") || "stage").replace(/-/g, " ")));
    for (const tie of kids(st, "tie")) {
      const win = attr(tie, "winner");
      const sides = kids(tie, "side").map((s) => h("div", { class: "side" + (attr(s, "competitor") === win ? " win" : "") },
        h("span", {}, nameOf(attr(s, "competitor"))), h("span", { class: "sc" }, attr(s, "score") || "")));
      const legs = kids(tie, "leg");
      const legStr = legs.length ? "legs: " + legs.map((l) => `${fmt(num(attr(l, "firstScore")))}–${fmt(num(attr(l, "secondScore")))}`).join("  ") : "";
      wrap.append(h("div", { class: "tie" }, ...sides, legStr ? h("div", { class: "legs" }, legStr) : null));
    }
    body.append(wrap);
  }
  return panel("Bracket", body);
}

function renderGames() {
  const gamesEl = kid(ROOT, "games"); const games = gamesEl ? kids(gamesEl, "game") : [];
  const rounds = [...new Set(games.map((g) => attr(g, "round")))];
  const filter = h("div", { class: "games-filter" });
  const sel = h("select", {}, h("option", { value: "" }, `All rounds (${games.length} games)`),
    ...rounds.map((r) => h("option", { value: r }, "Round " + r)));
  filter.append(h("span", { class: "mono", style: "font-size:.7rem;color:var(--ink-soft)" }, "FILTER"), sel);
  const listWrap = h("div", { class: "table-scroll" });
  const gv = h("div", {});
  const drawList = (round) => {
    const shown = round ? games.filter((g) => attr(g, "round") === round) : games;
    const rows = shown.map((g) => {
      const r = attr(g, "result");
      const cls = r === "1-0" ? "res-win" : r === "0-1" ? "res-win" : "res-draw";
      return h("tr", { class: "click", onclick: () => showGame(g) },
        h("td", { class: "num rank" }, attr(g, "round") + (attr(g, "board") ? "." + attr(g, "board") : "")),
        h("td", {}, nameOf(attr(g, "white"))), h("td", {}, nameOf(attr(g, "black"))),
        h("td", { class: cls }, r), h("td", { class: "mono", style: "color:var(--ink-soft)" }, txt(g, "eco") || ""));
    });
    listWrap.innerHTML = "";
    listWrap.append(h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Rd"), h("th", {}, "White"), h("th", {}, "Black"), h("th", {}, "Res"), h("th", {}, "ECO"))),
      h("tbody", {}, rows)));
  };
  sel.addEventListener("change", () => { gv.innerHTML = ""; drawList(sel.value); });
  drawList("");
  const showGame = (g) => { gv.innerHTML = ""; gv.append(gameViewer(g, () => (gv.innerHTML = ""))); gv.scrollIntoView({ behavior: "smooth", block: "start" }); };
  return panel(`Games (${games.length})`, h("div", { class: "body" }, filter, gv, listWrap));
}

/* ---------- game viewer (board + moves) ---------- */
function gameViewer(g, onClose) {
  const movesEl = kid(g, "moves");
  const moveEls = movesEl ? kids(movesEl, "move") : [];
  const uci = moveEls.map((m) => attr(m, "value"));
  const clocks = moveEls.map((m) => attr(m, "clockSeconds"));
  const startEl = kid(g, "start");
  const startFen = startEl && txt(startEl, "fen");
  const positions = [], sans = [];
  let ok = true;
  if (window.Chess) {
    try {
      const c = new Chess(startFen || undefined);
      positions.push(c.fen());
      for (const u of uci) {
        const mv = c.move({ from: u.slice(0, 2), to: u.slice(2, 4), promotion: u.slice(4, 5) || undefined });
        if (!mv) { ok = false; break; }
        sans.push(mv.san); positions.push(c.fen());
      }
    } catch (e) { ok = false; }
  } else ok = false;

  const back = h("button", { class: "ghost gv-back", onclick: () => onClose && onClose() });
  back.textContent = "✕ close game";
  const players = h("div", { class: "gv-players" },
    h("span", {}, "● " + nameOf(attr(g, "black"))), h("span", {}, nameOf(attr(g, "white")) + " ○"));
  const boardEl = h("div", { class: "board" });
  let ply = ok ? positions.length - 1 : 0;
  const drawBoard = () => renderFen(boardEl, positions[ply] || positions[0] || "8/8/8/8/8/8/8/8 w - - 0 1");
  const nav = h("div", { class: "gv-nav" },
    navBtn("⏮", () => { ply = 0; sync(); }), navBtn("◀", () => { if (ply > 0) ply--; sync(); }),
    navBtn("▶", () => { if (ply < positions.length - 1) ply++; sync(); }), navBtn("⏭", () => { ply = positions.length - 1; sync(); }));
  const left = h("div", {}, players, boardEl, nav,
    h("div", { class: "mono", style: "font-size:.75rem;color:var(--ink-soft)" }, `${attr(g, "result")}${txt(g, "termination") ? " · " + txt(g, "termination") : ""}`));

  const movelist = h("div", { class: "movelist" });
  if (!ok && uci.length) movelist.append(h("div", { class: "err" }, "Could not replay moves (non-standard start or chess.js unavailable). Showing UCI."),
    h("div", {}, uci.join(" ")));
  const mvSpans = [];
  const list = ok ? sans : [];
  for (let i = 0; i < list.length; i++) {
    if (i % 2 === 0) movelist.append(h("span", { class: "mvno" }, (i / 2 + 1) + "."));
    const clk = clocks[i] ? h("span", { class: "clk" }, " {" + hms(+clocks[i]) + "}") : null;
    const sp = h("span", { class: "mv", onclick: () => { ply = i + 1; sync(); } }, " " + list[i], clk);
    mvSpans.push(sp); movelist.append(sp);
    if (i % 2 === 1) movelist.append(document.createElement("br"));
  }
  function sync() {
    drawBoard();
    mvSpans.forEach((s, i) => s.classList.toggle("cur", i === ply - 1));
    const cur = mvSpans[ply - 1]; if (cur) cur.scrollIntoView({ block: "nearest" });
  }
  drawBoard();
  return h("div", {}, back, h("div", { class: "gameview" }, left, movelist));
}
function navBtn(label, fn) { const b = h("button", { class: "ghost", onclick: fn }); b.textContent = label; return b; }
function renderFen(el, fen) {
  el.innerHTML = "";
  const rows = fen.split(" ")[0].split("/");
  for (let r = 0; r < 8; r++) {
    let file = 0;
    for (const ch of rows[r]) {
      if (/\d/.test(ch)) { for (let k = 0; k < +ch; k++) { el.append(sq(r, file)); file++; } }
      else { const s = sq(r, file); const w = ch === ch.toUpperCase();
        s.append(h("span", { class: "pc " + (w ? "w" : "b") }, PIECE[ch.toLowerCase()])); el.append(s); file++; }
    }
  }
  function sq(r, f) { return h("div", { class: "sq " + ((r + f) % 2 === 0 ? "light" : "dark") }); }
}

/* ---------- sources ---------- */
function renderSources(srcs) {
  const body = h("div", { class: "body prose", style: "font-size:.85rem" });
  for (const s of srcs) body.append(h("p", {}, h("b", {}, (attr(s, "kind") || "source") + ": "),
    (txt(s, "note") || ""), txt(s, "uri") ? h("span", { class: "mono", style: "color:var(--ink-soft)" }, " " + txt(s, "uri")) : null));
  return collapsible("Sources", body);
}

/* ---------- panels ---------- */
function panel(title, ...body) { return h("section", { class: "panel" }, h("h2", {}, title), ...body); }
function collapsible(title, body, open) {
  const d = h("details", { class: "panel" }, h("summary", {}, title), body);
  if (open) d.open = true; return d;
}

/* ---------- exporters ---------- */
function doExport(kind) {
  if (kind === "ctml") return download(RAW, FILENAME, "application/xml");
  if (kind === "json") return download(JSON.stringify(xmlToJson(ROOT), null, 2), FILENAME.replace(/\.ctml$/, ".json"), "application/json");
  if (kind === "pgn") return download(toPgn(), FILENAME.replace(/\.ctml$/, ".pgn"), "application/x-chess-pgn");
}
function download(text, name, type) {
  const a = h("a", { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
}
function xmlToJson(el) {
  const o = {};
  for (const a of el.attributes) o["@" + a.name] = a.value;
  const childEls = [...el.children];
  if (!childEls.length) { const t = el.textContent.trim(); if (t) { if (!el.attributes.length) return t; o["#text"] = t; } return o; }
  for (const c of childEls) {
    const key = c.localName, val = xmlToJson(c);
    if (o[key] === undefined) o[key] = val;
    else { if (!Array.isArray(o[key])) o[key] = [o[key]]; o[key].push(val); }
  }
  return o;
}
function toPgn() {
  const hdr = kid(ROOT, "header"); const event = txt(hdr, "name") || "?";
  const place = kid(hdr, "placeRef"); const site = place ? (txt(place, "name") || "?") : "?";
  const gamesEl = kid(ROOT, "games"); const games = gamesEl ? kids(gamesEl, "game") : [];
  const fideById = {}; const parts = kid(ROOT, "participants");
  if (parts) for (const p of kids(parts, "participant")) {
    const ref = kid(p, "playerRef"), ids = ref && kid(ref, "ids");
    fideById[attr(p, "id")] = { name: nameOf(attr(p, "id")), fide: ids ? txt(ids, "fideId") : null,
      elo: kid(p, "ratingSnapshot") ? txt(kid(p, "ratingSnapshot"), "value") : null };
  }
  const out = [];
  for (const g of games) {
    const w = fideById[attr(g, "white")] || { name: nameOf(attr(g, "white")) };
    const b = fideById[attr(g, "black")] || { name: nameOf(attr(g, "black")) };
    const note = kid(g, "source") ? txt(kid(g, "source"), "note") || "" : "";
    const dm = note.match(/(?:End)?Date=(\d{4}\.\d{2}\.\d{2})/);
    const date = dm ? dm[1] : (dateDot(kid(kid(hdr, "dates"), "start")) || "????.??.??");
    const tc = kid(g, "timeControl") ? txt(kid(g, "timeControl"), "raw") : null;
    const tags = [["Event", event], ["Site", site], ["Date", date], ["Round", attr(g, "round") || "?"],
      ["White", w.name], ["Black", b.name], ["Result", attr(g, "result")]];
    if (txt(g, "eco")) tags.push(["ECO", txt(g, "eco")]);
    if (w.elo) tags.push(["WhiteElo", w.elo]); if (b.elo) tags.push(["BlackElo", b.elo]);
    if (w.fide) tags.push(["WhiteFideId", w.fide]); if (b.fide) tags.push(["BlackFideId", b.fide]);
    if (tc) tags.push(["TimeControl", tc]);
    const head = tags.map(([k, v]) => `[${k} "${(v == null ? "" : v).toString().replace(/"/g, "'")}"]`).join("\n");
    out.push(head + "\n\n" + movetext(g) + "\n");
  }
  return out.join("\n");
}
function movetext(g) {
  const movesEl = kid(g, "moves"); const uci = movesEl ? kids(movesEl, "move").map((m) => attr(m, "value")) : [];
  const result = attr(g, "result");
  if (!window.Chess || !uci.length) return result;
  const startEl = kid(g, "start"); const fen = startEl && txt(startEl, "fen");
  let c; try { c = new Chess(fen || undefined); } catch (e) { return result; }
  const parts = [];
  for (let i = 0; i < uci.length; i++) {
    const u = uci[i]; const mv = c.move({ from: u.slice(0, 2), to: u.slice(2, 4), promotion: u.slice(4, 5) || undefined });
    if (!mv) break;
    if (i % 2 === 0) parts.push((i / 2 + 1) + ".");
    parts.push(mv.san);
  }
  parts.push(result);
  return wrap(parts.join(" "), 80);
}

/* ---------- maker ---------- */
let Maker = null;
function wireMaker() {
  const pt = $("#playersTable tbody"), gt = $("#gamesTable tbody");
  const delBtn = () => h("button", { type: "button", class: "ghost", onclick: (e) => { e.target.closest("tr").remove(); refreshSelects(); } }, "✕");
  function refreshSelect(s) {
    const names = $$("#playersTable input[name=pname]").map((i) => i.value).filter(Boolean);
    const cur = s.value; s.innerHTML = "";
    names.forEach((n) => s.append(h("option", {}, n)));
    if (names.includes(cur)) s.value = cur;
  }
  function refreshSelects() { $$("select.gp").forEach(refreshSelect); }
  function playerSelect(val) {
    const s = h("select", { name: "gp", class: "gp" }); refreshSelect(s);
    if (val) { if (![...s.options].some((o) => o.value === val)) s.append(h("option", {}, val)); s.value = val; }
    return s;
  }
  function addPlayer(d = {}) {
    pt.append(h("tr", {},
      h("td", {}, h("input", { name: "pname", placeholder: "So, Wesley", value: d.name || "" })),
      h("td", {}, h("input", { name: "pfide", placeholder: "5202213", value: d.fide || "" })),
      h("td", {}, h("input", { name: "ptitle", placeholder: "GM", value: d.title || "" })),
      h("td", {}, h("input", { name: "prating", placeholder: "2765", value: d.rating || "" })),
      h("td", {}, h("input", { name: "pplace", placeholder: "#", style: "width:3rem", value: d.place || "" })),
      h("td", {}, h("input", { name: "pscore", placeholder: "pts", style: "width:3.5rem", value: d.score || "" })),
      h("td", {}, delBtn())));
  }
  function addGame(d = {}) {
    const rsel = h("select", { name: "gres" }, ["1-0", "0-1", "1/2-1/2", "*"].map((r) => h("option", {}, r)));
    rsel.value = d.result || "1-0";
    gt.append(h("tr", {},
      h("td", {}, h("input", { name: "grd", placeholder: "1", style: "width:3.5rem", value: d.round || "" })),
      h("td", {}, playerSelect(d.white)), h("td", {}, playerSelect(d.black)),
      h("td", {}, rsel),
      h("td", {}, h("input", { name: "gmoves", placeholder: "e4 e5 Nf3 …", value: d.moves || "" })),
      h("td", {}, delBtn())));
  }
  const gamePair = (tr) => { const s = tr.querySelectorAll("select.gp"); return [s[0].value, s[1].value]; };
  const samePair = (tr, a, b) => { const [w, k] = gamePair(tr); return (w === a && k === b) || (w === b && k === a); };
  function upsertPlayer(d) {
    const tr = $$("#playersTable tbody tr").find((r) => r.querySelector("[name=pname]").value.trim() === d.name);
    if (!tr) { addPlayer(d); return; }
    const fill = (sel, val) => { const inp = tr.querySelector(sel); if (val && !inp.value.trim()) inp.value = val; };
    fill("[name=pfide]", d.fide); fill("[name=ptitle]", d.title); fill("[name=prating]", d.rating);
    fill("[name=pplace]", d.place); fill("[name=pscore]", d.score);
  }
  const pairHasGame = (a, b) => $$("#gamesTable tbody tr").some((tr) => samePair(tr, a, b));
  const removeMovelessForPair = (a, b) => $$("#gamesTable tbody tr").forEach((tr) => { if (samePair(tr, a, b) && !tr.querySelector("[name=gmoves]").value.trim()) tr.remove(); });
  const hasGameKey = (round, w, b) => $$("#gamesTable tbody tr").some((tr) => { const [gw, gb] = gamePair(tr); return tr.querySelector("[name=grd]").value.trim() === round && gw === w && gb === b; });
  function pruneEmpty() {
    $$("#playersTable tbody tr").forEach((tr) => { if (!tr.querySelector("[name=pname]").value.trim()) tr.remove(); });
    $$("#gamesTable tbody tr").forEach((tr) => { const [w, b] = gamePair(tr); if (!w || !b) tr.remove(); });
  }
  const anyGames = () => $$("#gamesTable tbody tr").some((tr) => gamePair(tr)[0]);
  Maker = { addPlayer, addGame, upsertPlayer, pairHasGame, removeMovelessForPair, hasGameKey, pruneEmpty, anyGames, refreshSelects, reset: () => { pt.innerHTML = ""; gt.innerHTML = ""; } };
  $("#addPlayer").addEventListener("click", () => { addPlayer(); refreshSelects(); });
  $("#addGame").addEventListener("click", () => addGame());
  $("#playersTable").addEventListener("input", refreshSelects);
  addPlayer(); addPlayer(); addGame();
  $("#generate").addEventListener("click", generate);
  $("#makerDownload").addEventListener("click", () => download($("#makerOutput").textContent, "new-tournament.ctml", "application/xml"));
  $("#makerToViewer").addEventListener("click", () => loadText($("#makerOutput").textContent, "new-tournament.ctml"));
  $("#importPgnBtn").addEventListener("click", () => importPgn($("#importArea").value));
  $("#importXtBtn").addEventListener("click", () => importCrosstable($("#importArea").value));
  $("#importPgnFile").addEventListener("change", (e) => {
    const f = e.target.files[0]; if (!f) return;
    const r = new FileReader(); r.onload = () => { $("#importArea").value = r.result; importPgn(r.result); }; r.readAsText(f);
  });
}
function generate() {
  const f = $("#makerForm"), g = (n) => f.elements[n] ? f.elements[n].value.trim() : "";
  const players = $$("#playersTable tbody tr").map((tr) => ({
    name: tr.querySelector("[name=pname]").value.trim(), fide: tr.querySelector("[name=pfide]").value.trim(),
    title: tr.querySelector("[name=ptitle]").value.trim(), rating: tr.querySelector("[name=prating]").value.trim(),
    place: tr.querySelector("[name=pplace]").value.trim(), score: tr.querySelector("[name=pscore]").value.trim(),
  })).filter((p) => p.name);
  const idFor = {}; players.forEach((p, i) => (idFor[p.name] = p.fide ? "p-fide-" + p.fide : "p-" + slug(p.name) || "p" + i));
  const games = $$("#gamesTable tbody tr").map((tr) => ({
    round: tr.querySelector("[name=grd]").value.trim() || "1",
    white: tr.querySelectorAll("select.gp")[0].value, black: tr.querySelectorAll("select.gp")[1].value,
    result: tr.querySelector("[name=gres]").value, moves: tr.querySelector("[name=gmoves]").value.trim(),
  })).filter((x) => x.white && x.black && x.white !== x.black);
  const score = {}; players.forEach((p) => (score[p.name] = 0));
  for (const gm of games) { const [ws, bs] = scoreOf(gm.result); if (ws != null) { score[gm.white] += ws; score[gm.black] += bs; } }

  const E = (s) => (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  let x = `<?xml version='1.0' encoding='utf-8'?>\n<ctml:tournament xmlns:ctml="urn:ctml:2.0" ctmlVersion="2.1" id="tournament-${slug(g("name")) || "new"}">\n`;
  x += `  <ctml:header>\n    <ctml:name>${E(g("name"))}</ctml:name>\n    <ctml:eventType>${g("eventType")}</ctml:eventType>\n    <ctml:cadence>${g("cadence")}</ctml:cadence>\n`;
  x += `    <ctml:dates>\n${dayXml("start", g("start"))}${dayXml("end", g("end") || g("start"))}    </ctml:dates>\n`;
  if (g("city") || g("country")) {
    x += `    <ctml:placeRef ref="place:city:${E(g("country") || "XXX")}-${slug(g("city")) || "x"}" kind="city">\n`;
    if (g("city")) x += `      <ctml:name>${E(g("city"))}</ctml:name>\n`;
    if (g("country")) x += `      <ctml:country>${E(g("country").toUpperCase())}</ctml:country>\n`;
    if (g("city")) x += `      <ctml:city>${E(g("city"))}</ctml:city>\n`;
    x += `    </ctml:placeRef>\n`;
  }
  if (g("venue")) x += `    <ctml:venue>${E(g("venue"))}</ctml:venue>\n`;
  x += `  </ctml:header>\n  <ctml:participants>\n`;
  for (const p of players) {
    x += `    <ctml:participant id="${E(idFor[p.name])}">\n      <ctml:playerRef ref="${p.fide ? "player:fide:" + E(p.fide) : "player:name:" + slug(p.name)}">\n`;
    x += `        <ctml:name display="${E(p.name)}">`;
    if (p.name.includes(",")) { const [fam, giv] = p.name.split(",").map((s) => s.trim()); x += `<ctml:family>${E(fam)}</ctml:family>${giv ? `<ctml:given>${E(giv)}</ctml:given>` : ""}`; }
    else x += `<ctml:unstructured>${E(p.name)}</ctml:unstructured>`;
    x += `</ctml:name>\n`;
    if (p.title) x += `        <ctml:title>${E(p.title)}</ctml:title>\n`;
    if (p.fide) x += `        <ctml:ids><ctml:fideId>${E(p.fide)}</ctml:fideId></ctml:ids>\n`;
    x += `        <ctml:resolution method="${p.fide ? "fide-id" : "manual"}" resolver="ctml-web-maker/1"/>\n      </ctml:playerRef>\n`;
    if (p.rating) x += `      <ctml:ratingSnapshot system="fide" scope="standard"><ctml:value>${E(p.rating)}</ctml:value></ctml:ratingSnapshot>\n`;
    const computed = score[p.name]; const scoreVal = p.score !== "" ? p.score : String(computed % 1 ? computed : computed | 0);
    x += `      <ctml:score>${E(scoreVal)}</ctml:score>\n`;
    if (p.place) x += `      <ctml:placement>${E(p.place)}</ctml:placement>\n`;
    x += `    </ctml:participant>\n`;
  }
  x += `  </ctml:participants>\n  <ctml:games>\n`;
  let warn = "";
  games.forEach((gm, i) => {
    x += `    <ctml:game id="g-${i + 1}" round="${E(gm.round)}" white="${E(idFor[gm.white])}" black="${E(idFor[gm.black])}" result="${gm.result}">\n`;
    x += `      <ctml:start standard="true"/>\n`;
    const uci = gm.moves ? sanToUci(gm.moves) : null;
    if (gm.moves && !uci) warn = "Some move lists could not be parsed and were omitted.";
    if (uci && uci.length) {
      x += `      <ctml:moves notation="uci" plyCount="${uci.length}">\n`;
      uci.forEach((u, k) => (x += `        <ctml:move ply="${k + 1}" value="${u}"/>\n`));
      x += `      </ctml:moves>\n`;
    }
    x += `    </ctml:game>\n`;
  });
  x += `  </ctml:games>\n</ctml:tournament>\n`;
  const out = $("#makerOutput"); out.hidden = false; out.textContent = x;
  if (warn) { out.prepend(h("span", { class: "err" }, "⚠ " + warn + "\n\n")); }
  $("#makerDownload").disabled = false; $("#makerToViewer").disabled = false;
}
function sanToUci(movetext) {
  if (!window.Chess) return null;
  const toks = cleanMoves(movetext).replace(/\d+\.(\.\.)?/g, " ").replace(/[+#!?]/g, "").split(/\s+/).filter(Boolean);
  const c = new Chess(); const uci = [];
  for (const t of toks) { const mv = c.move(t, { sloppy: true }); if (!mv) return null; uci.push(mv.from + mv.to + (mv.promotion || "")); }
  return uci;
}

/* ---------- PGN import (fills the maker) ---------- */
function cleanMoves(s) {
  s = (s || "").replace(/\{[^}]*\}/g, "");                 // comments
  let prev; do { prev = s; s = s.replace(/\([^()]*\)/g, ""); } while (s !== prev); // variations
  return s.replace(/\$\d+/g, "").replace(/\b(1-0|0-1|1\/2-1\/2|\*)\b/g, " ").replace(/\s+/g, " ").trim();
}
function parsePgn(text) {
  const parts = (text || "").replace(/\r\n?/g, "\n").trim().split(/\n(?=\[Event\s)/);
  return parts.map((block) => {
    const headers = {}, moveLines = [];
    for (const line of block.split("\n")) {
      const m = line.match(/^\[(\w+)\s+"([^"]*)"\]/);
      if (m) headers[m[1]] = m[2]; else moveLines.push(line);
    }
    return { headers, moves: cleanMoves(moveLines.join(" ")) };
  }).filter((g) => g.headers.White || g.headers.Black);
}
function importPgn(text) {
  const out = $("#makerOutput");
  const games = parsePgn(text);
  if (!games.length) { out.hidden = false; out.textContent = "No games found in that PGN."; return; }
  const f = $("#makerForm"), set = (n, v) => { if (v != null && v !== "" && f.elements[n] && !String(f.elements[n].value).trim()) f.elements[n].value = v; };
  const h0 = games[0].headers;
  set("name", h0.Event);
  const dates = games.map((g) => g.headers.Date).filter((d) => /^\d{4}\.\d{2}\.\d{2}$/.test(d || "")).map((d) => d.replace(/\./g, "-")).sort();
  if (dates.length) { set("start", dates[0]); set("end", dates[dates.length - 1]); }
  else if (/^\d{4}\.\d{2}\.\d{2}$/.test(h0.EventDate || "")) set("start", h0.EventDate.replace(/\./g, "-"));
  if (h0.TimeControl) { const init = parseInt((h0.TimeControl.split("+")[0].split("/").pop() || "").replace(/\D+/g, ""), 10); if (init) set("cadence", init >= 3600 ? "classical" : init >= 600 ? "rapid" : "blitz"); }
  if (/^[A-Za-z]{3}$/.test(h0.EventCountry || "")) set("country", h0.EventCountry.toUpperCase());
  if (h0.Site && !/^\?/.test(h0.Site)) set("city", h0.Site.split(",")[0].replace(/\s+[A-Z]{2,3}$/, "").trim());
  const clean = (v) => (v && v !== "-1" && v !== "0" && v !== "?" ? v : "");
  const players = {};
  for (const g of games) for (const c of ["White", "Black"]) {
    const nm = g.headers[c]; if (!nm) continue;
    if (!players[nm]) players[nm] = { name: nm, fide: clean(g.headers[c + "FideId"]), title: g.headers[c + "Title"] || "", rating: clean(g.headers[c + "Elo"]) };
    else { const p = players[nm]; if (!p.fide) p.fide = clean(g.headers[c + "FideId"]); if (!p.title) p.title = g.headers[c + "Title"] || ""; if (!p.rating) p.rating = clean(g.headers[c + "Elo"]); }
  }
  const np = Object.keys(players).length;
  const et = (h0.EventType || "").toLowerCase();
  const type = /match/.test(et) ? "match" : /swiss/.test(et) ? "swiss" : /team/.test(et) ? "team"
    : /k\.?o|knock/.test(et) ? "knockout" : /rr|round|tourn/.test(et) ? "round-robin" : (np === 2 ? "match" : "round-robin");
  if (!Maker.anyGames() && [...f.elements.eventType.options].some((o) => o.value === type)) f.elements.eventType.value = type;
  Maker.pruneEmpty();
  Object.values(players).forEach((p) => Maker.upsertPlayer(p));
  Maker.refreshSelects();
  const normR = (r) => (["1-0", "0-1", "1/2-1/2"].includes(r) ? r : "*");
  let added = 0, merged = 0;
  for (const g of games) {
    const w = g.headers.White, b = g.headers.Black, round = g.headers.Round || "1";
    Maker.removeMovelessForPair(w, b);           // replace any result-only placeholder from a crosstable
    if (Maker.hasGameKey(round, w, b)) { merged++; continue; }
    Maker.addGame({ round, white: w, black: b, result: normR(g.headers.Result), moves: g.moves });
    added++;
  }
  out.hidden = false;
  out.textContent = `Imported ${np} player(s) and ${added} game(s)${merged ? ` (${merged} already present, skipped)` : ""}. Existing data was kept and merged. Review, then Generate.`;
  $("#makerDownload").disabled = true; $("#makerToViewer").disabled = true;
}

/* ---------- crosstable import (fills the maker; reconstructs games from a head-to-head grid) ---------- */
const XT_TITLES = new Set(["GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM", "AGM", "AIM", "AFM", "ACM", "WNM"]);
const CTML_TITLES = new Set(["GM", "IM", "FM", "CM", "WGM", "WIM", "WFM", "WCM", "NM"]);
function normScore(s) { let v = (s || "").trim().replace("½", ".5"); if (v.startsWith(".")) v = "0" + v; return v; }
function normCell(c) {
  c = (c || "").trim();
  if (c === "" || c === "*" || c === "-" || c === "·" || c === "•" || c === "X" || c === "x") return null;
  if (c === "½") return 0.5;
  const n = parseFloat(c.replace("½", ".5")); return isNaN(n) ? null : n;
}
function parseCrosstable(text) {
  const rows = [];
  for (const raw of (text || "").replace(/\r/g, "").split("\n")) {
    const cells = raw.split(/\t|\s{2,}/).map((c) => c.trim()).filter(Boolean);
    if (cells.length < 2 || !/^\d+$/.test(cells[0])) continue;
    const rank = parseInt(cells[0], 10);
    let ratingIdx = -1;
    for (let i = 1; i < cells.length; i++) { if (/^\d{3,4}$/.test(cells[i]) && +cells[i] >= 1000 && +cells[i] <= 3600) { ratingIdx = i; break; } }
    const end = ratingIdx > 0 ? ratingIdx : cells.length;
    let title = "", nameParts = [];
    for (let i = 1; i < end; i++) {
      let c = cells[i];
      if (/^(Avatar|of|medal)$/i.test(c)) continue;
      const up = c.toUpperCase();
      if (XT_TITLES.has(up)) { if (!title && CTML_TITLES.has(up)) title = up; continue; }
      const tm = c.match(/^(GM|IM|FM|WGM|WIM|WFM|CM|WCM|NM|AGM|AIM|AFM|ACM)\s+(.+)$/i);
      if (tm) { if (!title && CTML_TITLES.has(tm[1].toUpperCase())) title = tm[1].toUpperCase(); c = tm[2]; }
      if (/^[A-Z]{3}$/.test(c)) continue; // federation code column
      nameParts.push(c);
    }
    const name = nameParts.join(" ").replace(/\s+medal$/i, "").trim();
    const rating = ratingIdx > 0 ? cells[ratingIdx] : "";
    const ptsIdx = ratingIdx > 0 ? ratingIdx + 1 : -1;
    const points = (ptsIdx > 0 && ptsIdx < cells.length && /^[\d.,½]+$/.test(cells[ptsIdx])) ? normScore(cells[ptsIdx]) : "";
    const grid = (ptsIdx > 0 ? cells.slice(ptsIdx + 1) : []).map(normCell);
    if (name) rows.push({ rank, name, title, rating, points, grid });
  }
  return rows;
}
function importCrosstable(text) {
  const out = $("#makerOutput");
  const rows = parseCrosstable(text);
  if (rows.length < 2) {
    out.hidden = false;
    out.textContent = "Could not read a crosstable. Paste a tab- or column-separated table: rank, player, rating, points, and (optionally) a head-to-head grid.";
    return;
  }
  rows.sort((a, b) => a.rank - b.rank);
  const N = rows.length;
  const selfIncluded = rows.filter((r, i) => r.grid[i] === null).length >= Math.ceil(N / 2);
  let gridOk = rows.every((r) => r.grid.length >= (selfIncluded ? N : N - 1));
  if (gridOk) {
    rows.forEach((r, i) => {
      if (selfIncluded) r.g = r.grid.slice(0, N);
      else { const g = r.grid.slice(0, N - 1); g.splice(i, 0, null); r.g = g; }
    });
    if (rows.reduce((a, r) => a + r.g.filter((v) => v != null).length, 0) < N) gridOk = false;
  }
  const f = $("#makerForm");
  if (!Maker.anyGames() && [...f.elements.eventType.options].some((o) => o.value === "round-robin")) f.elements.eventType.value = "round-robin";
  Maker.pruneEmpty();
  rows.forEach((r) => Maker.upsertPlayer({ name: r.name, title: r.title, rating: r.rating, place: String(r.rank), score: r.points }));
  Maker.refreshSelects();
  let ng = 0, skipped = 0;
  if (gridOk) {
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      const v = rows[i].g[j]; if (v == null) continue;
      if (Maker.pairHasGame(rows[i].name, rows[j].name)) { skipped++; continue; } // a PGN already has this game
      Maker.addGame({ round: "", white: rows[i].name, black: rows[j].name, result: v === 1 ? "1-0" : v === 0 ? "0-1" : "1/2-1/2" });
      ng++;
    }
    Maker.refreshSelects();
  }
  out.hidden = false;
  out.textContent = `Imported ${N} player(s)` + (ng ? ` and reconstructed ${ng} game(s) from the head-to-head grid — colors are arbitrary.` : "")
    + (skipped ? ` ${skipped} game(s) already present (e.g. from a PGN) were kept as-is.` : (gridOk ? "" : " (no usable head-to-head grid detected, so no games were created)."))
    + ` Existing data was kept and merged. Fill in the event name and dates, then Generate.`;
  $("#makerDownload").disabled = true; $("#makerToViewer").disabled = true;
}

/* ---------- misc utils ---------- */
function num(x) { const n = parseFloat(x); return isNaN(n) ? Infinity : n; }
function fmt(v) { if (v == null || v === "") return ""; return v === 0.5 ? "½" : Number.isInteger(v) ? String(v) : (Math.floor(v) || "") + "½"; }
function scoreOf(r) { return r === "1-0" ? [1, 0] : r === "0-1" ? [0, 1] : r === "1/2-1/2" ? [0.5, 0.5] : r === "0-0" ? [0, 0] : [null, null]; }
function dateStr(el) { if (!el) return ""; const d = kid(el, "day"); if (d) return attr(d, "iso") || `${attr(d, "y")}-${attr(d, "m")}`; const m = kid(el, "month"); if (m) return `${attr(m, "y")}-${String(attr(m, "m")).padStart(2, "0")}`; const y = kid(el, "year"); return y ? attr(y, "y") : ""; }
function dateDot(el) { const s = dateStr(el); return s ? s.replace(/-/g, ".").padEnd(10, ".?") : null; }
function dayXml(which, iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `      <ctml:${which}><ctml:day y="${y}" m="${+m}" d="${+d}" iso="${iso}"/></ctml:${which}>\n`;
}
function slug(s) { return (s || "").normalize("NFKD").replace(/[^\w\s-]/g, "").trim().replace(/[\s_]+/g, "-").toLowerCase(); }
function hms(sec) { const H = Math.floor(sec / 3600), M = Math.floor((sec % 3600) / 60), S = sec % 60; return (H ? H + ":" + String(M).padStart(2, "0") : M) + ":" + String(S).padStart(2, "0"); }
function wrap(s, n) { const w = s.split(" "); let line = "", out = ""; for (const t of w) { if ((line + " " + t).length > n) { out += line + "\n"; line = t; } else line = line ? line + " " + t : t; } return out + line; }
})();
