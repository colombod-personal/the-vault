"""MCP Apps views: small HTML pages a host (Claude, ChatGPT, VS Code, ...) shows inside the chat, next
to a tool's result (``ui://`` resources, ``text/html;profile=mcp-app``; the ``io.modelcontextprotocol/ui``
extension). Each tool that has a view names it in ``_meta.ui.resourceUri`` (only for a client that advertised the
extension at ``initialize``: mcp_session.py); a host without MCP Apps shows the tool's normal text answer, so nothing
depends on the view.

Rules the views keep (docs/compliance.md):
- Every third-party value is shown with where it came from: the footer lists the result's ``provenance``
  blocks, repeats the Fan Content notice, and says what the Vault computed.
- Card images are Scryfall's, shown whole with the artist credited. Prices are dated and not a store's.
- Text from tool results is only ever inserted as text (never as HTML), links open through the host,
  and the views load nothing but the card image (``resourceDomains`` below).
"""

from __future__ import annotations

import json

from .. import provenance as prov
from ..deck_tools import FORMATS

MIME = "text/html;profile=mcp-app"
URI_PREFIX = "ui://vault/"
PROTOCOL = "2026-01-26"
SCRYFALL_IMAGES = "https://cards.scryfall.io"

CSS = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1b1b1f; --muted: #5c5c66; --line: #d9d9e0; --card: #f6f6f9; --accent: #7a5a00; --ok: #1b7a3d; --bad: #b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg: #17171a; --fg: #ededf0; --muted: #a2a2ad; --line: #34343b; --card: #202025; --accent: #e0b64a; --ok: #5fd08a; --bad: #ff8a80; } }
:root[data-theme="light"] { --bg: #ffffff; --fg: #1b1b1f; --muted: #5c5c66; --line: #d9d9e0; --card: #f6f6f9; --accent: #7a5a00; --ok: #1b7a3d; --bad: #b3261e; }
:root[data-theme="dark"] { --bg: #17171a; --fg: #ededf0; --muted: #a2a2ad; --line: #34343b; --card: #202025; --accent: #e0b64a; --ok: #5fd08a; --bad: #ff8a80; }
* { box-sizing: border-box; }
body { margin: 0; padding: 12px; font: 14px/1.5 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
h1 { font-size: 18px; margin: 0 0 4px; } h2 { font-size: 14px; margin: 16px 0 6px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.muted { color: var(--muted); } .small { font-size: 12px; } .ok { color: var(--ok); } .bad { color: var(--bad); }
.row { display: flex; gap: 14px; flex-wrap: wrap; } .grow { flex: 1 1 260px; min-width: 0; }
.chip { display: inline-block; padding: 1px 8px; margin: 2px 4px 2px 0; border: 1px solid var(--line); border-radius: 999px; font-size: 12px; background: var(--card); }
.chip.ok { border-color: var(--ok); } .chip.bad { border-color: var(--bad); }
.box { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin: 8px 0; background: var(--card); }
.oracle { white-space: pre-wrap; }
img.card { width: 220px; max-width: 100%; border-radius: 10px; display: block; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 10px; margin: 8px 0; }
.tile { border: 1px solid var(--line); border-radius: 8px; padding: 6px; } img.thumb { width: 100%; border-radius: 7px; display: block; }
button, select, input[type=range] { font: inherit; } button { padding: 4px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--card); color: var(--fg); cursor: pointer; }
button.primary { border-color: var(--accent); }
table { border-collapse: collapse; width: 100%; } td, th { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar { height: 10px; background: var(--accent); border-radius: 3px; min-width: 1px; } .barrow { display: grid; grid-template-columns: 28px 1fr 34px; gap: 8px; align-items: center; margin: 2px 0; }
details { margin: 4px 0; } summary { cursor: pointer; }
ol.steps { padding-left: 22px; } ol.steps li { margin: 8px 0; }
footer { margin-top: 16px; padding-top: 10px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
footer p { margin: 3px 0; }
a.ext { color: var(--accent); cursor: pointer; text-decoration: underline; }
"""

# The protocol and the helpers every view shares. Nothing here builds HTML from data: text only.
SHARED_JS = r"""
var VIEW = "__VIEW__", PROTOCOL = "__PROTOCOL__";
var pending = {}, nextId = 1, lastInput = {}, lastResult = null, hostContext = {};
function send(m) { window.parent.postMessage(m, "*"); }
function request(method, params) {
  return new Promise(function (resolve, reject) {
    var id = nextId++; pending[id] = { resolve: resolve, reject: reject };
    send({ jsonrpc: "2.0", id: id, method: method, params: params || {} });
  });
}
function notify(method, params) { send({ jsonrpc: "2.0", method: method, params: params || {} }); }
function callTool(name, args) {
  return request("tools/call", { name: name, arguments: args }).then(function (res) {
    if (res && res.isError) { throw new Error(textOf(res) || "The tool reported an error"); }
    return payload(res);
  });
}
function textOf(res) { var c = (res && res.content) || []; for (var i = 0; i < c.length; i++) { if (c[i].type === "text") { return c[i].text; } } return ""; }
function payload(res) {
  if (res && res.structuredContent) { return res.structuredContent; }
  try { return JSON.parse(textOf(res)); } catch (e) { return null; }
}
function h(tag, attrs, kids) {
  var node = document.createElement(tag);
  Object.keys(attrs || {}).forEach(function (k) {
    if (k === "class") { node.className = attrs[k]; } else if (k === "text") { node.textContent = attrs[k]; }
    else if (k.slice(0, 2) === "on") { node.addEventListener(k.slice(2), attrs[k]); } else { node.setAttribute(k, attrs[k]); }
  });
  (kids || []).forEach(function (kid) { if (kid !== null && kid !== undefined) { node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid); } });
  return node;
}
function ext(url, label) {
  return h("a", { class: "ext", text: label || url, role: "link", tabindex: "0", onclick: function () { request("ui/open-link", { url: url }).catch(function () {}); } });
}
function usd(v) { return v === null || v === undefined ? "no price" : "$" + Number(v).toFixed(2); }
function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } return node; }
function applyContext(ctx) {
  hostContext = Object.assign(hostContext, ctx || {});
  if (ctx && ctx.theme) { document.documentElement.setAttribute("data-theme", ctx.theme); }
  var vars = ctx && ctx.styles && ctx.styles.variables;
  if (vars) { Object.keys(vars).forEach(function (k) { if (/^--[a-z0-9-]+$/i.test(k) && typeof vars[k] === "string" && !/[;{}]/.test(vars[k])) { document.documentElement.style.setProperty(k, vars[k]); } }); }
}
function provenanceFooter(blocks) {
  var f = h("footer", {}, []);
  var notices = {};
  (blocks || []).forEach(function (b) {
    if (b.notice) { notices[b.notice] = true; }
    var line = [];
    if (b.kind === "computed") {
      line.push("Computed by The Vault: " + (b.origin || "a result") + (b.as_of ? ", " + b.as_of : "") + ".");
      (b.inputs || []).forEach(function (i) { if (i.notice) { notices[i.notice] = true; } });
      var from = (b.inputs || []).map(function (i) { return i.source + (i.version ? " (" + i.version + ")" : "") + (i.as_of ? " " + i.as_of : ""); });
      if (from.length) { line.push(" From: " + from.join("; ") + "."); }
      f.appendChild(h("p", { text: line.join("") }));
    } else {
      line.push("Source: " + b.source + (b.origin ? " (" + b.origin + ")" : "") + (b.as_of ? ", as of " + b.as_of : "") + (b.version ? ", " + b.version : "") + ". ");
      var p = h("p", {}, [line.join("")]);
      if (b.url && /^https:\/\//.test(b.url)) { p.appendChild(ext(b.url, "link")); }
      f.appendChild(p);
    }
  });
  Object.keys(notices).forEach(function (n) { f.appendChild(h("p", { text: n })); });
  f.appendChild(h("p", { text: "Shown by The Vault, a free fan tool. It is not produced or endorsed by Scryfall, Wizards of the Coast or any source named above." }));
  return f;
}
function deckHeader(deck) {
  // Which deck this is: its name, then format, commander(s), card count and colour identity (text only, #216).
  if (!deck) { return null; }
  var o = deck.overview || {}, cmd = o.commanders || [], bits = [];
  bits.push(o.format ? o.format.charAt(0).toUpperCase() + o.format.slice(1) : "Format not given");
  if (cmd.length) { bits.push((cmd.length > 1 ? "Commanders: " : "Commander: ") + cmd.join(" & ")); }
  if (o.cards !== null && o.cards !== undefined) { bits.push(o.cards + " cards"); }
  if (o.color_identity) { bits.push("colour identity " + o.color_identity); }
  var box = h("div", { class: "box" }, [h("h1", { text: deck.name || (cmd.length ? cmd.join(" & ") : "Pasted deck") }), h("div", { text: bits.join(" \u00b7 ") })]);
  if (o.format_from && o.format_from !== "set on the deck") { box.appendChild(h("div", { class: "small muted", text: "Format read from the list (" + o.format_from + "): confirm it if it matters." })); }
  return box;
}
function deckArgs(env) {
  // The deck the tool was asked about, in the form the tools take: a saved deck's id or the pasted list. The answer's own
  // deck block carries the id too, for a host that does not forward the tool's input. Null when neither is known.
  var i = lastInput || {};
  if (i.deck_id !== undefined && i.deck_id !== null) { return { deck_id: i.deck_id }; }
  if (typeof i.text === "string" && i.text) { return { text: i.text }; }
  var d = env && env.deck;
  if (d && d.id !== undefined && d.id !== null) { return { deck_id: d.id }; }
  return null;
}
function signed(v) { var x = Math.round(Number(v) * 100) / 100; return (x < 0 ? "−$" : "+$") + Math.abs(x).toFixed(2); }
function combosBody(into, env) {
  // Combos from Commander Spellbook (find_combos): theirs, written by their community, each with a link to its page.
  var r = env.result || env;
  var t = r.totals || {};
  into.appendChild(h("p", { text: (t.included || 0) + " combo(s) in this deck, " + (t.almost_included || 0) + " one card short. Listed by Commander Spellbook." }));
  function group(title, list, short) {
    if (!(list || []).length) { return; }
    into.appendChild(h("h2", { text: title }));
    list.forEach(function (c) {
      var box = h("div", { class: "box" }, [h("div", {}, [h("strong", { text: c.cards.join(" + ") })])]);
      if (short && (c.missing || []).length) { box.appendChild(h("div", { class: "bad", text: "Missing: " + c.missing.join(", ") })); }
      if ((c.produces || []).length) { box.appendChild(h("div", { text: "Produces: " + c.produces.join(", ") })); }
      if (c.mana_needed) { box.appendChild(h("div", { class: "small", text: "Mana needed: " + c.mana_needed })); }
      if (c.prerequisites) { box.appendChild(h("div", { class: "small muted", text: "Prerequisites: " + c.prerequisites })); }
      if (c.description) { box.appendChild(h("div", { class: "small oracle", text: c.description })); }
      box.appendChild(h("div", { class: "small muted", text: "From Commander Spellbook" + (c.bracket_tag ? " (bracket tag " + c.bracket_tag + ")" : "") + ". " }, []));
      if (c.url && /^https:\/\//.test(c.url)) { box.lastChild.appendChild(ext(c.url, "Open this combo")); }
      into.appendChild(box);
    });
  }
  group("Combos in the deck", r.included, false);
  group("One card short", r.almost_included, true);
  if (r.limits) { into.appendChild(h("p", { class: "small muted", text: r.limits })); }
  (r.notes || []).forEach(function (n) { into.appendChild(h("p", { class: "small muted", text: n })); });
}
function reportSize() {
  var send_ = function () { notify("ui/notifications/size-changed", { width: Math.ceil(document.documentElement.scrollWidth), height: Math.ceil(document.documentElement.scrollHeight) }); };
  if (window.ResizeObserver) { new ResizeObserver(send_).observe(document.body); }
  send_();
}
function show(result) { lastResult = result; var root = clear(document.getElementById("root")); if (!result) { root.appendChild(h("p", { class: "muted", text: "The tool returned no data to show." })); return; } render(root, result); }
window.addEventListener("message", function (e) {
  if (e.source !== window.parent) { return; }
  var m = e.data;
  if (!m || m.jsonrpc !== "2.0") { return; }
  if (m.id !== undefined && m.method === undefined) {
    var p = pending[m.id]; if (!p) { return; } delete pending[m.id];
    if (m.error) { p.reject(new Error(m.error.message || "request failed")); } else { p.resolve(m.result); }
    return;
  }
  if (m.method === "ui/notifications/tool-input") { lastInput = (m.params && m.params.arguments) || {}; }
  else if (m.method === "ui/notifications/tool-result") { show(payload(m.params)); }
  else if (m.method === "ui/notifications/host-context-changed") { applyContext(m.params); }
  else if (m.method === "ui/resource-teardown" && m.id !== undefined) { send({ jsonrpc: "2.0", id: m.id, result: {} }); }
});
request("ui/initialize", { protocolVersion: PROTOCOL, appInfo: { name: "the-vault-" + VIEW, version: "1" }, appCapabilities: { availableDisplayModes: ["inline"] } })
  .then(function (r) { applyContext((r && r.hostContext) || {}); notify("ui/notifications/initialized", {}); reportSize(); })
  .catch(function () { document.getElementById("root").appendChild(h("p", { class: "muted", text: "This view needs a host that supports MCP Apps; the tool's text answer has the same information." })); });
"""

CARD_JS = r"""
function render(root, r) {
  if (!r.card) {
    root.appendChild(h("h1", { text: "No exact match" }));
    var box = h("div", { class: "box" }, [h("p", { text: "Did you mean:" })]);
    (r.suggestions || []).forEach(function (name) {
      box.appendChild(h("button", { text: name, onclick: function () { callTool("get_card_oracle", { name: name }).then(show).catch(function (e) { root.appendChild(h("p", { class: "bad", text: e.message })); }); } }));
      box.appendChild(document.createTextNode(" "));
    });
    root.appendChild(box); root.appendChild(provenanceFooter(r.provenance)); return;
  }
  var c = r.card, left = h("div", {}, []), right = h("div", { class: "grow" }, []);
  if (c.image_normal && /^https:\/\/cards\.scryfall\.io\//.test(c.image_normal)) {
    left.appendChild(h("img", { class: "card", src: c.image_normal, alt: c.name + " (card image)", referrerpolicy: "no-referrer" }));
    left.appendChild(h("p", { class: "small muted", text: (c.artist ? "Illustrated by " + c.artist + ". " : "") + "Image: Scryfall. Card images and names are property of Wizards of the Coast." }));
  }
  right.appendChild(h("h1", { text: c.name + (c.mana_cost ? "  " + c.mana_cost : "") }));
  right.appendChild(h("div", { class: "muted", text: c.type_line || "" }));
  var faces = (c.faces || []).filter(function (f) { return f.oracle_text || f.type_line; });
  if (faces.length > 1) {
    // a transform, modal double-faced, split, adventure or flip card: every face's own text, never only the first (#249)
    faces.forEach(function (f) {
      right.appendChild(h("h2", { text: f.name + (f.mana_cost ? "  " + f.mana_cost : "") }));
      if (f.type_line) { right.appendChild(h("div", { class: "muted", text: f.type_line })); }
      if (f.oracle_text) { right.appendChild(h("div", { class: "box oracle", text: f.oracle_text })); }
      if (f.power !== null && f.power !== undefined) { right.appendChild(h("div", { text: "Power/Toughness: " + f.power + "/" + f.toughness })); }
      if (f.loyalty) { right.appendChild(h("div", { text: "Loyalty: " + f.loyalty })); }
    });
  } else {
    if (c.oracle_text) { right.appendChild(h("div", { class: "box oracle", text: c.oracle_text })); }
    if (c.power !== null && c.power !== undefined) { right.appendChild(h("div", { text: "Power/Toughness: " + c.power + "/" + c.toughness })); }
    if (c.loyalty) { right.appendChild(h("div", { text: "Loyalty: " + c.loyalty })); }
  }
  if (r.price) {
    right.appendChild(h("p", { text: usd(r.price.usd) + " (cheapest priced paper printing; " + r.price.source + ", as of " + r.price.as_of + "; not a store's price today)" }));
  }
  right.appendChild(h("h2", { text: "Legal in" }));
  var leg = h("div", {}, []), keys = Object.keys(c.legalities || {});
  keys.forEach(function (k) { var v = c.legalities[k]; if (v === "legal" || v === "restricted") { leg.appendChild(h("span", { class: "chip ok", text: k + (v === "restricted" ? " (restricted)" : "") })); } });
  var banned = keys.filter(function (k) { return c.legalities[k] === "banned"; });
  if (banned.length) { leg.appendChild(h("div", { class: "small bad", text: "Banned in: " + banned.join(", ") })); }
  right.appendChild(leg);
  if ((r.tags || []).length) {
    right.appendChild(h("h2", { text: "Scryfall Tagger tags (community opinion, not rules)" }));
    var tg = h("div", {}, []); r.tags.forEach(function (t) { tg.appendChild(h("span", { class: "chip", title: "weight: " + (t.weight || "unknown"), text: t.tag })); }); right.appendChild(tg);
  }
  var rulings = h("div", {}, []);
  if (r.rulings_total) {
    right.appendChild(h("button", { text: "Show rulings (" + r.rulings_total + ")", onclick: function (ev) {
      ev.target.disabled = true;
      callTool("get_rulings", { oracle_id: c.oracle_id, limit: 8 }).then(function (rr) {
        clear(rulings);
        (rr.rulings || []).forEach(function (x) { rulings.appendChild(h("div", { class: "box" }, [h("div", { class: "small muted", text: (x.published_at || "") + " · " + x.source }), h("div", { text: x.comment })])); });
        if (rr.total > (rr.rulings || []).length) { rulings.appendChild(h("p", { class: "small muted", text: "Showing the newest " + rr.rulings.length + " of " + rr.total + ". Ask for more in the chat." })); }
        rulings.appendChild(provenanceFooter(rr.provenance));
      }).catch(function (e) { rulings.appendChild(h("p", { class: "bad", text: e.message })); });
    } }));
  }
  right.appendChild(rulings);
  root.appendChild(h("div", { class: "row" }, [left, right]));
  if (c.scryfall_uri && /^https:\/\//.test(c.scryfall_uri)) { root.appendChild(h("p", {}, [ext(c.scryfall_uri, "View this card on Scryfall")])); }
  root.appendChild(provenanceFooter(r.provenance));
}
"""

DECK_JS = r"""
var FORMATS = __FORMATS__;
function legalityBlock(root, env) {
  // Legality is shown on opening: the deck's own format when it has one (a saved deck's, or read from the list),
  // else a chooser. Works for a saved deck (deck_id) as for a pasted list.
  var base = deckArgs(env), o = (env.deck && env.deck.overview) || {};
  var known = FORMATS.indexOf(o.format) >= 0 ? o.format : "";
  var out = h("div", {}, []);
  var sel = h("select", {}, [h("option", { value: "", text: "Choose a format" })].concat(FORMATS.map(function (f) { return h("option", { value: f, text: f }); })));
  sel.value = known;
  function check() {
    if (!sel.value) { clear(out).appendChild(h("p", { class: "muted", text: "Choose a format to check this deck against." })); return; }
    if (!base) { clear(out).appendChild(h("p", { class: "muted", text: "This host did not pass the deck to the view: ask the assistant to run deck_legality." })); return; }
    clear(out).appendChild(h("p", { class: "muted", text: "Checking..." }));
    callTool("deck_legality", Object.assign({}, base, { format: sel.value })).then(function (lr) {
      var x = lr.result; clear(out);
      out.appendChild(h("p", { class: x.legal ? "ok" : "bad", text: x.legal ? "Legal in " + x.format + " (as far as this check goes)." : "Not legal in " + x.format + ": " + x.issues.length + " issue(s)." }));
      x.issues.forEach(function (i) { out.appendChild(h("div", { class: "small", text: "• " + (i.card ? i.card + ": " : "") + i.detail })); });
      out.appendChild(h("p", { class: "small muted", text: "Not checked: " + x.not_checked.join("; ") }));
      out.appendChild(provenanceFooter(lr.provenance));
    }).catch(function (e) { clear(out).appendChild(h("p", { class: "bad", text: e.message })); });
  }
  root.appendChild(h("h2", { text: "Legality" + (known ? " in " + known : "") }));
  if (known && o.format_from && o.format_from !== "set on the deck") { root.appendChild(h("div", { class: "small muted", text: "Format read from the list (" + o.format_from + "): change it below if it is wrong." })); }
  root.appendChild(h("div", {}, [sel, document.createTextNode(" "), h("button", { class: "primary", text: "Check", onclick: check })]));
  root.appendChild(out);
  check();
}
function combosBlock(root, env) {
  var base = deckArgs(env), out = h("div", {}, []);
  root.appendChild(h("h2", { text: "Combos (Commander Spellbook)" }));
  root.appendChild(h("button", { text: "Find combos", onclick: function (ev) {
    if (!base) { clear(out).appendChild(h("p", { class: "muted", text: "This host did not pass the deck to the view: ask the assistant to run find_combos." })); return; }
    ev.target.disabled = true;
    clear(out).appendChild(h("p", { class: "muted", text: "Asking Commander Spellbook..." }));
    callTool("find_combos", base).then(function (cr) {
      clear(out); combosBody(out, cr); out.appendChild(provenanceFooter(cr.provenance));
    }).catch(function (e) { ev.target.disabled = false; clear(out).appendChild(h("p", { class: "bad", text: e.message })); });
  } }));
  root.appendChild(h("span", { class: "small muted", text: " Asked on demand: the Vault keeps no copy of their data." }));
  root.appendChild(out);
}
function render(root, env) {
  var r = env.result || env;
  var head = deckHeader(env.deck); if (head) { root.appendChild(head); }
  root.appendChild(h("h2", { text: "Statistics: " + r.cards + " cards (" + r.unique + " unique)" }));
  root.appendChild(h("div", { class: "muted", text: r.lands + " lands, " + r.nonland + " other cards, average mana value " + r.average_mana_value_nonland + ", color identity " + ((r.color_identity || []).join("") || "colorless") }));
  root.appendChild(h("h2", { text: "Mana curve (non-land)" }));
  var max = Math.max.apply(null, Object.keys(r.curve).map(function (k) { return r.curve[k]; }).concat([1]));
  Object.keys(r.curve).forEach(function (k) {
    var bar = h("div", { class: "bar", style: "width:" + Math.round((r.curve[k] / max) * 100) + "%" });
    root.appendChild(h("div", { class: "barrow" }, [h("span", { class: "muted", text: k }), h("div", {}, [bar]), h("span", { class: "num", text: String(r.curve[k]) })]));
  });
  root.appendChild(h("h2", { text: "Roles (Scryfall Tagger tags: a community's opinion)" }));
  var t = h("table", {}, [h("tr", {}, [h("th", { text: "Role" }), h("th", { class: "num", text: "Cards" }), h("th", { text: "Which" })])]);
  Object.keys(r.roles).forEach(function (role) {
    var names = r.roles[role].cards.map(function (c) { return c.name; }).join(", ");
    t.appendChild(h("tr", {}, [h("td", { text: role.replace("_", " ") }), h("td", { class: "num", text: String(r.roles[role].count) }), h("td", { class: "small", text: names })]));
  });
  root.appendChild(t);
  root.appendChild(h("p", { text: "Estimated cost: " + usd(r.estimated_cost_usd) + " (" + r.priced_cards + " priced, " + r.unpriced_cards + " not). " + (r.price_note || "") }));
  if ((r.unmatched || []).length) { root.appendChild(h("p", { class: "bad", text: "Not found in the catalog: " + r.unmatched.join(", ") })); }
  legalityBlock(root, env);
  combosBlock(root, env);
  root.appendChild(provenanceFooter(env.provenance));
}
"""

COMBOS_JS = r"""
function render(root, env) {
  var head = deckHeader(env.deck); if (head) { root.appendChild(head); }
  root.appendChild(h("h2", { text: "Combos in this deck" }));
  combosBody(root, env);
  root.appendChild(provenanceFooter(env.provenance));
}
"""

UPGRADES_JS = r"""
// The plan is a list of swaps: { add: candidate or null, cut: cut candidate or null }. It survives a re-render
// (the budget slider asks find_upgrades again), so it lives outside render().
var plan = [], validation = null, redrawPlan = null;
function planAdds() { return plan.filter(function (s) { return s.add; }).map(function (s) { return s.add.name; }); }
function planCuts() { return plan.filter(function (s) { return s.cut; }).map(function (s) { return s.cut.name; }); }
function planKey(budget) { return JSON.stringify([planAdds(), planCuts(), budget]); }
function swapDelta(s) {
  // The price difference of one swap: the added card's price minus the cut card's, both from the tool's answers.
  var a = s.add ? s.add.price_usd : 0, c = s.cut ? s.cut.price_usd : 0;
  if (s.add && (a === null || a === undefined)) { return "Price change unknown: no price for " + s.add.name + "."; }
  if (s.cut && (c === null || c === undefined)) { return "Price change unknown: no price for " + s.cut.name + "."; }
  if (s.add && s.cut) { return "Price change " + signed(a - c) + " (adds " + usd(a) + ", the cut card is " + usd(c) + ")"; }
  if (s.add) { return "Price change " + signed(a) + " (nothing cut)"; }
  return "Price change " + signed(-c) + " (cut only)";
}
function validate(base, format, budget) {
  var key = planKey(budget);
  return callTool("validate_deck_changes", Object.assign({}, base, { format: format, adds: planAdds(), cuts: planCuts(), budget_usd: budget }))
    .then(function (vr) { validation = { key: key, vr: vr }; if (redrawPlan) { redrawPlan(); } })
    .catch(function (e) { validation = { key: key, error: e.message }; if (redrawPlan) { redrawPlan(); } });
}
function render(root, env) {
  var r = env.result || env, base = deckArgs(env);
  var head = deckHeader(env.deck); if (head) { root.appendChild(head); }
  root.appendChild(h("h2", { text: "Upgrade candidates (" + r.format + ")" }));
  var budget = h("input", { type: "range", min: "0", max: String(Math.max(50, Math.ceil(r.budget_usd * 3))), step: "1", value: String(r.budget_usd) });
  var label = h("span", { text: " budget: $" + r.budget_usd });
  budget.addEventListener("input", function () { label.textContent = " budget: $" + budget.value; });
  budget.addEventListener("change", function () {
    // One budget: no added card costs more than it, and the adds together stay within it. Moving it asks the validator
    // about the current plan again and asks find_upgrades for candidates at the new budget (the plan is kept).
    var b = Number(budget.value);
    if (!base) { return; }
    if (plan.length) { validate(base, r.format, b); }
    callTool("find_upgrades", Object.assign({}, lastInput, base, { format: r.format, budget_usd: b })).then(function (nr) {
      lastInput = Object.assign({}, lastInput, base, { format: r.format, budget_usd: b }); show(nr);
    }).catch(function (e) { root.appendChild(h("p", { class: "bad", text: e.message })); });
  });
  root.appendChild(h("div", {}, [budget, label, h("span", { class: "small muted", text: " in total for the adds; each candidate costs at most this" })]));
  if (!base) { root.appendChild(h("p", { class: "small muted", text: "This host did not pass the deck to the view, so the budget and the plan check are off: ask the assistant." })); }
  if (r.deck_cost_usd !== null && r.deck_cost_usd !== undefined) { root.appendChild(h("p", { class: "small muted", text: "The deck's estimated cost now: " + usd(r.deck_cost_usd) })); }
  var planBox = h("div", { class: "box" }, []);
  var addBoxes = {}, cutBoxes = {};
  function cutOf(name) { return (r.cut_candidates || []).filter(function (c) { return c.name === name; })[0] || null; }
  function usedCut(name) { return plan.some(function (s) { return s.cut && s.cut.name === name; }); }
  function sync() {
    Object.keys(addBoxes).forEach(function (n) { addBoxes[n].checked = plan.some(function (s) { return s.add && s.add.name === n; }); });
    Object.keys(cutBoxes).forEach(function (n) { cutBoxes[n].checked = usedCut(n); });
  }
  function changed() { validation = null; drawPlan(); sync(); }
  function drawPlan() {
    clear(planBox);
    planBox.appendChild(h("strong", { text: "Your plan" }));
    if (!plan.length) { planBox.appendChild(h("div", { class: "muted", text: "Nothing chosen yet. Tick cards to add below; each add is paired with a card to cut." })); }
    plan.forEach(function (s, i) {
      var row = h("div", { class: "box" }, []);
      var title = s.add ? "Add " + s.add.name + " (" + usd(s.add.price_usd) + ")" : "Cut " + s.cut.name + " (" + usd(s.cut.price_usd) + ")";
      row.appendChild(h("div", {}, [h("strong", { text: title }), document.createTextNode(" "), h("button", { text: "Remove", onclick: function () { plan.splice(i, 1); changed(); } })]));
      if (s.add) {
        var pick = h("select", {}, [h("option", { value: "", text: "No cut for this one" })].concat((r.cut_candidates || []).map(function (c) { return h("option", { value: c.name, text: c.name + " (" + usd(c.price_usd) + ")" }); })));
        pick.value = s.cut ? s.cut.name : "";
        pick.addEventListener("change", function () {
          var chosen = pick.value ? cutOf(pick.value) : null;
          if (chosen) { plan.forEach(function (o) { if (o !== s && o.cut && o.cut.name === chosen.name) { o.cut = null; } }); }
          s.cut = chosen; plan = plan.filter(function (o) { return o.add || o.cut; }); changed();
        });
        row.appendChild(h("div", {}, [document.createTextNode("Cut: "), pick]));
      }
      row.appendChild(h("div", { class: "small", text: swapDelta(s) }));
      planBox.appendChild(row);
    });
    if (plan.length && base) {
      planBox.appendChild(h("button", { class: "primary", text: "Check this plan", onclick: function () {
        validation = null; drawPlan(); validate(base, r.format, Number(budget.value));
      } }));
    }
    var v = validation && validation.key === planKey(Number(budget.value)) ? validation : null;
    if (v && v.error) { planBox.appendChild(h("p", { class: "bad", text: v.error })); }
    else if (v) {
      var x = v.vr.result;
      planBox.appendChild(h("p", { class: x.valid ? "ok" : "bad", text: (x.valid ? "Valid. " : "Not valid. ") + "The adds cost " + usd(x.added_cost_usd) + (x.budget_usd !== null ? " of " + usd(x.budget_usd) : "") + "; the deck has " + x.cards_after + " cards." }));
      planBox.appendChild(h("p", { text: "Net price change: " + signed(x.net_change_usd) + " (adds " + usd(x.added_cost_usd) + ", cuts " + usd(x.cut_value_usd) + "). The deck: " + usd(x.deck_cost_before_usd) + " before, " + usd(x.deck_cost_after_usd) + " after." }));
      if ((x.cut_unpriced || []).length) { planBox.appendChild(h("p", { class: "small muted", text: "No price for the cut " + x.cut_unpriced.join(", ") + ": counted as $0.00." })); }
      planBox.appendChild(h("p", { class: "small muted", text: "The budget counts the adds only: cuts are not refunded against it." }));
      x.issues.forEach(function (i) { planBox.appendChild(h("div", { class: "small", text: "• " + (i.card ? i.card + ": " : "") + i.detail })); });
      (x.existing_issues || []).forEach(function (i) { planBox.appendChild(h("div", { class: "small muted", text: "The deck already had: " + (i.card ? i.card + ": " : "") + i.detail })); });
      planBox.appendChild(provenanceFooter(v.vr.provenance));
    }
  }
  redrawPlan = drawPlan;
  root.appendChild(planBox);
  Object.keys(r.candidates).forEach(function (role) {
    root.appendChild(h("h2", { text: "Add: " + role.replace("_", " ") + " (have " + (r.gaps[role] ? r.gaps[role].have : "?") + ")" }));
    var list = r.candidates[role];
    if (!list.length) { root.appendChild(h("p", { class: "muted", text: "No candidates within this budget and the deck's colors." })); }
    list.forEach(function (c) {
      var box = h("input", { type: "checkbox" }); addBoxes[c.name] = box;
      box.addEventListener("change", function () {
        if (box.checked) {
          var free = (r.cut_candidates || []).filter(function (k) { return !usedCut(k.name); })[0] || null;  // the least played first: a suggestion, changeable in the plan
          plan.push({ add: c, cut: free });
        } else { plan = plan.filter(function (s) { return !(s.add && s.add.name === c.name); }); }
        changed();
      });
      root.appendChild(h("label", { class: "box", style: "display:block" }, [box, document.createTextNode(" "), h("strong", { text: c.name }), document.createTextNode("  " + usd(c.price_usd) + " (" + (c.price_date || "") + ")  rank " + (c.edhrec_rank === null ? "n/a" : c.edhrec_rank)), h("div", { class: "small muted", text: c.why })]));
    });
  });
  if ((r.cut_candidates || []).length) {
    root.appendChild(h("h2", { text: "Cut candidates (least played, no role tag)" }));
    r.cut_candidates.forEach(function (c) {
      var box = h("input", { type: "checkbox" }); cutBoxes[c.name] = box;
      box.addEventListener("change", function () {
        if (box.checked) { plan.push({ add: null, cut: c }); }
        else { plan.forEach(function (s) { if (s.cut && s.cut.name === c.name) { s.cut = null; } }); plan = plan.filter(function (s) { return s.add || s.cut; }); }
        changed();
      });
      root.appendChild(h("label", { style: "display:block" }, [box, document.createTextNode(" " + c.name + " (" + usd(c.price_usd) + ", rank " + (c.edhrec_rank === null ? "n/a" : c.edhrec_rank) + ")")]));
    });
  }
  (r.notes || []).forEach(function (n) { root.appendChild(h("p", { class: "small muted", text: n })); });
  root.appendChild(provenanceFooter(env.provenance));
  drawPlan(); sync();
}
"""

STEPS_JS = r"""
function render(root, r) {
  root.appendChild(h("h1", { text: r.title || "Step by step" }));
  if ((r.cards || []).length) { root.appendChild(h("div", { class: "muted", text: "Cards: " + r.cards.join(", ") })); }
  root.appendChild(h("div", { class: "small muted", text: "Comprehensive Rules, edition " + (r.version || "unknown") + ". Step text is the assistant's own wording; each rule is looked up by The Vault." }));
  var ol = h("ol", { class: "steps" }, []);
  r.steps.forEach(function (s) {
    var li = h("li", {}, [h("div", { text: s.text })]);
    s.rules.forEach(function (rule) { li.appendChild(h("details", {}, [h("summary", { text: "Rule " + rule.number }), h("div", { class: "box oracle", text: rule.text })])); });
    ol.appendChild(li);
  });
  root.appendChild(ol);
  if ((r.unknown_rules || []).length) {
    root.appendChild(h("div", { class: "box" }, [h("strong", { class: "bad", text: "Rule numbers not found in this edition" }),
      h("div", { text: r.unknown_rules.map(function (u) { return "step " + u.step + ": " + u.rule; }).join("; ") }), h("div", { class: "small muted", text: "Treat those steps with caution." })]));
  }
  root.appendChild(provenanceFooter(r.provenance));
}
"""

SHOPPING_JS = r"""
function listTexts(r) {
  // The paste-ready text(s) the tool returned: its default list and, when the tool offers several store formats
  // (an array or an object under formats / exports / texts), each of those. Whatever is returned can be shown, copied, saved.
  var out = [];
  var several = r.texts && typeof r.texts === "object";  // format "all": r.text is only the plain one of r.texts
  if (typeof r.text === "string" && !several) { out.push({ label: lastInput && lastInput.format ? "List (" + lastInput.format + ")" : "Plain list", key: r.format, text: r.text }); }
  ["formats", "exports", "texts"].forEach(function (k) {
    var v = r[k];
    if (Array.isArray(v)) { v.forEach(function (x) { if (x && typeof x.text === "string") { out.push({ label: String(x.label || x.name || x.format || k), text: x.text }); } }); }
    else if (v && typeof v === "object") {
      Object.keys(v).forEach(function (n) {
        var t = typeof v[n] === "string" ? v[n] : v[n] && typeof v[n].text === "string" ? v[n].text : null;
        if (t !== null) { out.push({ label: (k === "texts" && r.store_format && r.store_format[n] && r.store_format[n].store) || n, key: n, text: t }); }
      });
    }
  });
  return out;
}
function fileName(label) { return "shopping-list" + (label && label !== "Plain list" ? "-" + label.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "") + ".txt"; }
function saveText(name, text) {
  // A file download from the page itself (a Blob link). A sandboxed frame may not allow downloads: nothing reports that, so
  // the text stays on screen and the message says what to do. Returns false only when the browser could not even try.
  try {
    var url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    var a = document.createElement("a"); a.setAttribute("href", url); a.setAttribute("download", name);
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
    return true;
  } catch (e) { return false; }
}
function render(root, env) {
  var r = env.result || env;
  var head = deckHeader(env.deck); if (head) { root.appendChild(head); }
  root.appendChild(h("h2", { text: r.lines.length ? "To buy: " + r.lines.length + " card(s), about " + usd(r.total_usd) : "You own everything in this list" }));
  if (r.lines.length) {
    var t = h("table", {}, [h("tr", {}, [h("th", { class: "num", text: "Qty" }), h("th", { text: "Card" }), h("th", { class: "num", text: "Each" }), h("th", { text: "Price date" })])]);
    r.lines.forEach(function (l) {
      t.appendChild(h("tr", {}, [h("td", { class: "num", text: String(l.quantity) }), h("td", { text: l.name + (l.known_card ? "" : " (not found in the catalog: check the name)") }), h("td", { class: "num", text: usd(l.unit_price_usd) }), h("td", { class: "small", text: l.price_date || "" })]));
    });
    root.appendChild(t);
    if (r.unpriced_lines) { root.appendChild(h("p", { class: "muted", text: r.unpriced_lines + " line(s) have no known price." })); }
    var texts = listTexts(r), current = texts[0] || { label: "Plain list", text: "" };
    var sf = r.store_format || {}, one = sf.store ? sf : null;  // format "all": sf holds each store's entry, by format
    var ta = h("textarea", { readonly: "readonly", rows: String(Math.min(12, r.lines.length + 1)), style: "width:100%;font-family:monospace" }); ta.value = current.text;
    root.appendChild(h("h2", { text: "List to paste into " + (one ? one.store : "a store's own list or deck tool") }));
    var limits = h("p", { class: "small muted", text: (one || sf[current.key] || {}).limits || "" });
    if (texts.length > 1) {
      var which = h("select", {}, texts.map(function (x, i) { return h("option", { value: String(i), text: x.label }); }));
      which.addEventListener("change", function () { current = texts[Number(which.value)]; ta.value = current.text; limits.textContent = (sf[current.key] || {}).limits || ""; });
      root.appendChild(h("div", {}, [document.createTextNode("Format: "), which]));
    }
    root.appendChild(ta);
    root.appendChild(limits);
    var msg = h("span", { class: "small muted", text: "" });
    root.appendChild(h("button", { text: "Copy list", onclick: function () { ta.select(); try { document.execCommand("copy"); msg.textContent = " Copied."; } catch (e) { msg.textContent = " Select the text and copy it."; } } }));
    root.appendChild(document.createTextNode(" "));
    root.appendChild(h("button", { text: "Download as a text file", onclick: function () {
      msg.textContent = saveText(fileName(current.label), current.text) ? " Download started. If no file appears, this host blocks downloads: use Copy list or select the text above." : " This host blocks downloads: use Copy list or select the text above.";
    } }));
    root.appendChild(msg);
  }
  (r.notes || []).forEach(function (n) { root.appendChild(h("p", { class: "small muted", text: n })); });
  root.appendChild(provenanceFooter(env.provenance));
}
"""

PRINTINGS_JS = r"""
var FINISH = { nonfoil: "non-foil", foil: "foil", etched: "etched foil" };
function scryfallImage(img) { return img && /^https:\/\/cards\.scryfall\.io\//.test(img.small || img.url) ? (img.small || img.url) : null; }
function picture(img, alt) {
  var src = scryfallImage(img); if (!src) { return null; }
  return h("div", {}, [h("img", { class: "thumb", src: src, alt: alt, referrerpolicy: "no-referrer" }),
    h("div", { class: "small muted", text: (img.artist ? "Illustrated by " + img.artist + ". " : "") + "Image: Scryfall." })]);
}
function linesWith(index, change) {
  return (lastInput.lines || []).map(function (l, i) { return i === index ? Object.assign({}, l, change) : l; });
}
function choose(root, index, text, change, status) {
  status.textContent = "Sending your choice...";
  // The choice goes into the chat as the person's message, so the assistant previews again with it.
  request("ui/message", { role: "user", content: [{ type: "text", text: text }] }).then(function () {
    status.textContent = "Sent: “" + text + "”";
  }).catch(function () {
    // A host that can't take messages: preview it here (it changes nothing) and say what to tell the assistant.
    callTool("update_owned_cards", { lines: linesWith(index, change) }).then(function (r) {
      lastInput = { lines: linesWith(index, change) }; show(r);
      document.getElementById("root").insertBefore(h("div", { class: "box", text: "Tell the assistant: “" + text + "”" }), document.getElementById("root").firstChild);
    }).catch(function (e) { status.textContent = e.message; });
  });
}
function gallery(root, r) {
  if (!r.card) {
    root.appendChild(h("h1", { text: "No card with that name" }));
    if ((r.did_you_mean || []).length) { root.appendChild(h("p", { text: "Did you mean: " + r.did_you_mean.join(", ") + "?" })); }
    root.appendChild(provenanceFooter(r.provenance)); return;
  }
  root.appendChild(h("h1", { text: r.printings.length ? "Your " + r.card + ": " + r.copies + " cop" + (r.copies === 1 ? "y" : "ies") + " in " + r.printings.length + " printing" + (r.printings.length === 1 ? "" : "s") : "You don't own " + r.card }));
  var grid = h("div", { class: "grid" }, []);
  r.printings.forEach(function (c) {
    var name = (c.set_name || String(c.set).toUpperCase()) + " #" + c.number;
    var tile = h("div", { class: "tile" }, [picture(c.image, r.card + ", " + name), h("div", { text: name }),
      h("span", { class: "chip ok", text: c.owned + " " + (FINISH[c.finish] || c.finish || "") })]);
    if (c.image && c.image.scryfall_uri && /^https:\/\//.test(c.image.scryfall_uri)) { tile.appendChild(h("div", { class: "small" }, [ext(c.image.scryfall_uri, "On Scryfall")])); }
    grid.appendChild(tile);
  });
  root.appendChild(grid);
  root.appendChild(provenanceFooter(r.provenance));
}
function render(root, r) {
  if (r.printings !== undefined) { gallery(root, r); return; }
  var lines = r.lines || [];
  var asking = lines.filter(function (l) { return l.status === "choose_printing"; }).length;
  root.appendChild(h("h1", { text: asking ? "Which printing?" : r.ready ? "Ready to apply: nothing has changed yet" : "These changes can't be applied as they are" }));
  if (r.refused) { root.appendChild(h("p", { class: "bad", text: r.refused })); }
  lines.forEach(function (l, index) {
    var box = h("div", { class: "box" }, []);
    var verb = l.action === "add" ? "Add " + l.quantity : l.action === "remove" ? "Remove " + l.quantity : "Set to " + l.quantity;
    box.appendChild(h("strong", { text: verb + " × " + (l.card || l.name) }));
    if (l.status === "choose_printing") {
      var status = h("p", { class: "small muted", text: "Tap the one you have." });
      box.appendChild(status);
      var grid = h("div", { class: "grid" }, []);
      (l.choose_from || []).forEach(function (c) {
        var name = (c.set_name || String(c.set).toUpperCase()) + " #" + c.number;
        var tile = h("div", { class: "tile" }, [picture(c.image, (l.card || l.name) + ", " + name),
          h("div", { text: name }), c.owned ? h("span", { class: "chip ok", text: "you own " + c.owned + (c.finish ? " " + (FINISH[c.finish] || c.finish) : "") }) : null]);
        (c.finishes || [c.finish || "nonfoil"]).forEach(function (f) {
          tile.appendChild(h("button", { text: FINISH[f] || f, onclick: function () {
            choose(root, index, "For " + (l.card || l.name) + ": it's the " + name + ", " + (FINISH[f] || f) + ".",
              { set: c.set, number: c.number, finish: f, printing_unknown: false }, status);
          } }));
          tile.appendChild(document.createTextNode(" "));
        });
        grid.appendChild(tile);
      });
      box.appendChild(grid);
      var pg = l.printings;
      if (pg && pg.page < pg.pages) {
        box.appendChild(h("button", { text: "More printings (" + pg.page + " of " + pg.pages + ")", onclick: function () {
          callTool("update_owned_cards", { lines: linesWith(index, { printings_page: pg.page + 1 }) }).then(function (r2) {
            lastInput = { lines: linesWith(index, { printings_page: pg.page + 1 }) }; show(r2);
          }).catch(function (e) { status.textContent = e.message; });
        } }));
        box.appendChild(document.createTextNode(" "));
      }
      if (pg && pg.scryfall_unavailable) { box.appendChild(h("p", { class: "small muted", text: "Scryfall did not answer, so only the printings the Vault already knows are shown." })); }
      if (l.action === "add") {
        box.appendChild(h("button", { text: "I don't know which one", onclick: function () {
          choose(root, index, "For " + (l.card || l.name) + ": I don't know which printing.", { printing_unknown: true }, status);
        } }));
      }
    } else if (l.status === "ready") {
      var row = h("div", { class: "row" }, []);
      var pic = picture(l.image, l.card); if (pic) { row.appendChild(h("div", { style: "width:150px" }, [pic])); }
      var p = l.printing && typeof l.printing === "object" ? String(l.printing.set).toUpperCase() + " #" + l.printing.number + ", " + (FINISH[l.printing.finish] || l.printing.finish) : "printing not specified";
      row.appendChild(h("div", { class: "grow" }, [h("div", { text: p }), h("div", { text: "Copies: " + l.copies_before + " → " + l.copies_after }),
        h("div", { class: "small muted", text: l.unit_price_usd ? usd(l.unit_price_usd) + " each (Scryfall's market price, not a store's)" : "No known price" })]));
      box.appendChild(row);
    } else {
      box.appendChild(h("p", { class: "bad", text: l.reason || "Can't be applied" }));
      if ((l.did_you_mean || []).length) { box.appendChild(h("p", { text: "Did you mean: " + l.did_you_mean.join(", ") + "?" })); }
    }
    root.appendChild(box);
  });
  if (r.ready) {
    root.appendChild(h("p", { text: "+" + r.copies_added + " / −" + r.copies_removed + " copies, value change " + usd(r.value_change_usd) + ". Say yes in the chat to apply it; it can be undone." }));
  }
  root.appendChild(provenanceFooter(r.provenance));
}
"""

VIEWS = {
    "card": {"title": "Card", "js": CARD_JS, "images": True},
    "printings": {"title": "Owned cards update", "js": PRINTINGS_JS, "images": True},
    "deck": {"title": "Deck dashboard", "js": DECK_JS},
    "combos": {"title": "Combos", "js": COMBOS_JS},
    "upgrades": {"title": "Upgrade candidates", "js": UPGRADES_JS},
    "steps": {"title": "Step by step", "js": STEPS_JS},
    "shopping": {"title": "Shopping list", "js": SHOPPING_JS},
}


def uri(view: str) -> str:
    return URI_PREFIX + view


def html(view: str) -> str:
    spec = VIEWS[view]
    script = SHARED_JS.replace("__VIEW__", view).replace("__PROTOCOL__", PROTOCOL) + spec["js"].replace("__FORMATS__", json.dumps(list(FORMATS)))
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{spec['title']} · The Vault</title><style>{CSS}</style></head>\n"
            f'<body><div id="root"><p class="muted">Loading...</p></div>\n<script>{script}</script></body></html>\n')


def resource_meta(view: str) -> dict:
    """Content-security metadata for the host: no network at all, except Scryfall's image server for the card view."""
    csp = {"resourceDomains": [SCRYFALL_IMAGES]} if VIEWS[view].get("images") else {}
    return {"ui": {"csp": csp, "prefersBorder": True}}


def resources() -> list[dict]:
    return [{"uri": uri(v), "name": f"vault_{v}", "title": f"The Vault: {s['title']}", "mimeType": MIME,
             "description": f"{s['title']} view for the matching Vault tool (MCP Apps)."} for v, s in VIEWS.items()]


def read(resource_uri: str) -> dict | None:
    view = resource_uri[len(URI_PREFIX):] if resource_uri.startswith(URI_PREFIX) else None
    if view not in VIEWS:
        return None
    return {"contents": [{"uri": resource_uri, "mimeType": MIME, "text": html(view), "_meta": resource_meta(view)}]}


NOTICE = prov.FAN_CONTENT_NOTICE
