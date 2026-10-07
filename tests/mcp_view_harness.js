// Runs one MCP Apps view (the <script> of a page from vault/api/mcp_ui.py) against a tiny DOM and a fake host, then prints
// what a person would have seen and what the view asked the host for (tests/test_mcp_apps_run.py drives it).
//
//   node tests/mcp_view_harness.js <view-script.js> <scenario.json>
//
// scenario: { input: tool arguments, result: the tool's structuredContent, tools: { name: answer | [answer, ...] },
//             steps: [ { action: "click"|"change"|"check", tag, text, type, nth, value } ], blockDownloads: bool }
// An answer { "__error": "message" } is a tool error. Output: { snapshots: [ { text, controls, calls, ... } ] }, one after
// loading and one after each step. This is a stub, not a browser: it has no layout, no CSS and no sandbox. It checks that the
// view's code runs on realistic data, builds what it should, and calls the tools it should with the arguments it should.
"use strict";
const fs = require("fs");

const BLOCK = new Set(["div", "p", "h1", "h2", "tr", "li", "label", "footer", "table", "ol", "details", "summary", "button"]);

class TextNode {
  constructor(data) { this.data = String(data); this.parentNode = null; }
  get textContent() { return this.data; }
}
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.tag = tag; this.children = []; this.parentNode = null; this.attributes = {};
    this.className = ""; this.listeners = {}; this.disabled = false; this.checked = false; this._value = undefined;
    this.style = { setProperty() {} }; this.scrollWidth = 600; this.scrollHeight = 400;
  }
  get firstChild() { return this.children[0] || null; }
  get lastChild() { return this.children[this.children.length - 1] || null; }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set textContent(v) { this.children = []; if (String(v) !== "") { this.appendChild(new TextNode(v)); } }
  get value() {
    if (this._value !== undefined) { return this._value; }
    if (this.tag === "select") { const o = this.find((e) => e.tag === "option")[0]; return o ? (o.attributes.value !== undefined ? o.attributes.value : o.textContent) : ""; }
    return "";
  }
  set value(v) { this._value = String(v); }
  appendChild(n) { if (n.parentNode) { n.parentNode.removeChild(n); } n.parentNode = this; this.children.push(n); return n; }
  removeChild(n) { const i = this.children.indexOf(n); if (i >= 0) { this.children.splice(i, 1); n.parentNode = null; } return n; }
  insertBefore(n, ref) { if (n.parentNode) { n.parentNode.removeChild(n); } n.parentNode = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) { this.children.push(n); } else { this.children.splice(i, 0, n); } return n; }
  setAttribute(k, v) { this.attributes[k] = String(v); if (k === "value") { this._value = String(v); } }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type) { const ev = { type, target: this, preventDefault() {} }; (this.listeners[type] || []).forEach((fn) => fn(ev)); }
  select() { selected = this.value; }
  click() {
    if (this.tag === "a" && this.attributes.download !== undefined) { downloads.push({ name: this.attributes.download, text: blobs[this.attributes.href] }); }
    this.dispatch("click");
  }
  find(pred, out = []) { this.children.forEach((c) => { if (c instanceof Element) { if (pred(c)) { out.push(c); } c.find(pred, out); } }); return out; }
}

let selected = "", copied = [], downloads = [], blobs = {}, blobSeq = 0;
const root = new Element("div"); root.attributes.id = "root";
const loading = new Element("p"); loading.textContent = "Loading..."; root.appendChild(loading);
const body = new Element("body"); body.appendChild(root);
const doc = {
  createElement: (t) => new Element(t), createTextNode: (t) => new TextNode(t), getElementById: (id) => (id === "root" ? root : null),
  documentElement: new Element("html"), body, execCommand: (c) => { if (c === "copy") { copied.push(selected); } return true; },
};

const scenario = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const script = fs.readFileSync(process.argv[2], "utf8");
const listeners = [], fromView = [], errors = [];
const parent = {
  postMessage(m) {
    fromView.push(m);
    if (m.id === undefined) { return; }
    setImmediate(() => reply(m));
  },
};
function deliver(data) { listeners.forEach((fn) => fn({ source: parent, data })); }
function reply(m) {
  if (m.method === "ui/initialize") { return deliver({ jsonrpc: "2.0", id: m.id, result: { protocolVersion: "2026-01-26", hostContext: { theme: "light" } } }); }
  if (m.method === "tools/call") {
    const queue = scenario.tools && scenario.tools[m.params.name];
    if (queue === undefined) { return deliver({ jsonrpc: "2.0", id: m.id, error: { message: "the test host has no answer for " + m.params.name } }); }
    const answer = Array.isArray(queue) ? (queue.length > 1 ? queue.shift() : queue[0]) : queue;
    if (answer && answer.__error) { return deliver({ jsonrpc: "2.0", id: m.id, result: { isError: true, content: [{ type: "text", text: answer.__error }] } }); }
    return deliver({ jsonrpc: "2.0", id: m.id, result: { content: [{ type: "text", text: JSON.stringify(answer) }], structuredContent: answer } });
  }
  return deliver({ jsonrpc: "2.0", id: m.id, result: {} });  // ui/open-link, ui/message
}

const win = { parent, addEventListener: (t, fn) => { if (t === "message") { listeners.push(fn); } } };
class Blob { constructor(parts) { this.text = parts.join(""); } }
const URLStub = { createObjectURL(b) { if (scenario.blockDownloads) { throw new Error("downloads are blocked"); } const u = "blob:test/" + (++blobSeq); blobs[u] = b.text; return u; }, revokeObjectURL() {} };

function text(n) {
  if (n instanceof TextNode) { return n.data; }
  let out = n.children.map(text).join("");
  if (n.tag === "textarea") { out += n._value || ""; }
  return BLOCK.has(n.tag) ? out + "\n" : out;
}
function controls() {
  return root.find((e) => ["button", "select", "input", "textarea", "a"].includes(e.tag)).map((e) => ({
    tag: e.tag, type: e.attributes.type || null, text: e.tag === "select" ? e.find((o) => o.tag === "option").map((o) => o.textContent).join("|") : e.textContent,
    value: e.tag === "button" || e.tag === "a" ? null : e.value, checked: e.checked, disabled: e.disabled, max: e.attributes.max || null,
  }));
}
function snapshot() {
  return { text: text(root), controls: controls(), calls: fromView.filter((m) => m.method === "tools/call").map((m) => ({ name: m.params.name, arguments: m.params.arguments })),
           requests: fromView.filter((m) => m.id !== undefined).map((m) => m.method), notifications: fromView.filter((m) => m.id === undefined).map((m) => m.method),
           copied: copied.slice(), downloads: downloads.slice(), errors: errors.slice() };
}
async function settle() { for (let i = 0; i < 12; i++) { await new Promise((r) => setImmediate(r)); } }
function pick(step) {
  const hits = root.find((e) => (!step.tag || e.tag === step.tag) && (!step.type || e.attributes.type === step.type)
    && (step.text === undefined || e.textContent.includes(step.text) || (e.tag === "select" && e.find((o) => o.textContent === step.text).length)));
  const el = hits[step.nth || 0];
  if (!el) { throw new Error("no element for " + JSON.stringify(step)); }
  return el;
}

process.on("unhandledRejection", (e) => { errors.push("unhandled rejection: " + String(e && e.stack || e)); });
(async () => {
  const snapshots = [];
  try {
    new Function("window", "document", "URL", "Blob", script)(win, doc, URLStub, Blob);
    await settle();
    deliver({ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: { arguments: scenario.input || {} } });
    if (scenario.result !== undefined) { deliver({ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: { structuredContent: scenario.result, content: [] } }); }
    await settle();
    snapshots.push(snapshot());
    for (const step of scenario.steps || []) {
      const el = pick(step);
      if (step.action === "click") { el.click(); }
      else if (step.action === "change") { el.value = step.value; el.dispatch("input"); el.dispatch("change"); }
      else if (step.action === "check") { el.checked = step.value !== false; el.dispatch("change"); }
      else { throw new Error("unknown step " + step.action); }
      await settle();
      snapshots.push(snapshot());
    }
  } catch (e) { errors.push(String(e && e.stack || e)); snapshots.push(snapshot()); }
  process.stdout.write(JSON.stringify({ snapshots, errors }));
})();
