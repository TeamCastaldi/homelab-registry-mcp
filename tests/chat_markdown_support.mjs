// Test-only support for tests/chat_markdown.test.mjs (`node --test`).
//
// Loads the markdown parser/DOM-builder straight out of
// src/registry_mcp/chat/static/index.html (the single source of truth --
// nothing here duplicates that logic) by extracting its
// <script nonce="__CSP_NONCE__"> block and running it in a `vm` context
// against a hand-rolled fake `document`. No jsdom, no npm dependency: this
// is deliberately just enough of the DOM surface
// (createElement/createTextNode/createDocumentFragment, appendChild,
// textContent, className, href/target/rel, querySelector[All]) for the
// createElement/textContent-only builder in index.html to run against.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const INDEX_HTML_PATH = path.join(
  __dirname,
  "..",
  "src",
  "registry_mcp",
  "chat",
  "static",
  "index.html"
);

// Matches the opening tag by its nonce attribute rather than an exact
// string, so a harmless index.html formatting change (attribute order,
// extra whitespace, an added attribute) doesn't break this extraction.
const OPEN_TAG_RE = /<script\b[^>]*\bnonce="__CSP_NONCE__"[^>]*>/g;
const CLOSE_TAG = "</script>";
const MARKDOWN_RENDER_MARKER = "// ---- markdown-render:start ----";

// index.html has two nonce'd script tags: the markdown renderer and the
// app wiring. Selected by content (the marker comment the production file
// already carries for this exact purpose), not by document position, so
// this keeps working even if another nonce'd script tag is ever added
// earlier in the document.
function extractMarkdownRenderScript(html) {
  OPEN_TAG_RE.lastIndex = 0;
  let match;
  while ((match = OPEN_TAG_RE.exec(html)) !== null) {
    const start = match.index + match[0].length;
    const end = html.indexOf(CLOSE_TAG, start);
    if (end === -1) throw new Error("index.html: unterminated <script> block");
    const content = html.slice(start, end);
    if (content.includes(MARKDOWN_RENDER_MARKER)) return content;
  }
  throw new Error("index.html: could not find the markdown-render script block");
}

function escapeText(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}
function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

class FakeNode {
  constructor(nodeType) {
    this.nodeType = nodeType;
    this.parentNode = null;
  }
}

class FakeText extends FakeNode {
  constructor(value) {
    super(3);
    this.value = String(value);
  }
  get textContent() {
    return this.value;
  }
  get outerHTML() {
    return escapeText(this.value);
  }
}

function matchesSelector(node, sel) {
  if (node.nodeType !== 1) return false;
  if (sel.startsWith(".")) {
    return (node.attributes["class"] || "").split(/\s+/).includes(sel.slice(1));
  }
  return node._tag === sel;
}

class FakeElement extends FakeNode {
  constructor(tag) {
    super(1);
    this._tag = tag.toLowerCase();
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.attributes = {};
  }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name)
      ? this.attributes[name]
      : null;
  }
  set className(v) {
    this.attributes["class"] = v;
  }
  get className() {
    return this.attributes["class"] || "";
  }
  set href(v) {
    this.attributes["href"] = v;
  }
  get href() {
    return this.attributes["href"];
  }
  set target(v) {
    this.attributes["target"] = v;
  }
  set rel(v) {
    this.attributes["rel"] = v;
  }
  set textContent(v) {
    this.childNodes = [];
    if (v !== "") this.childNodes.push(new FakeText(v));
  }
  get textContent() {
    return this.childNodes.map((c) => c.textContent).join("");
  }
  querySelector(sel) {
    const stack = [...this.childNodes];
    while (stack.length) {
      const n = stack.shift();
      if (matchesSelector(n, sel)) return n;
      if (n.childNodes) stack.unshift(...n.childNodes);
    }
    return null;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      n.childNodes.forEach((c) => {
        if (matchesSelector(c, sel)) out.push(c);
        if (c.childNodes) walk(c);
      });
    };
    walk(this);
    return out;
  }
  get outerHTML() {
    const attrs = Object.entries(this.attributes)
      .map(([k, v]) => ` ${k}="${escapeAttr(v)}"`)
      .join("");
    const inner = this.childNodes.map((c) => c.outerHTML).join("");
    return `<${this._tag}${attrs}>${inner}</${this._tag}>`;
  }
}

class FakeFragment extends FakeNode {
  constructor() {
    super(11);
    this.childNodes = [];
  }
  appendChild(child) {
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }
  querySelectorAll(sel) {
    const out = [];
    const walk = (n) => {
      n.childNodes.forEach((c) => {
        if (matchesSelector(c, sel)) out.push(c);
        if (c.childNodes) walk(c);
      });
    };
    walk(this);
    return out;
  }
  get outerHTML() {
    return this.childNodes.map((c) => c.outerHTML).join("");
  }
}

export function makeFakeDocument() {
  return {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (v) => new FakeText(v),
    createDocumentFragment: () => new FakeFragment(),
  };
}

// Recursively collects every FakeElement in a built fragment/element,
// including the root itself if it is one -- for adversarial assertions
// like "no <script> element anywhere" or "no on* attribute anywhere".
export function collectElements(root) {
  const out = [];
  const walk = (n) => {
    if (n.nodeType === 1) out.push(n);
    (n.childNodes || []).forEach(walk);
  };
  walk(root);
  return out;
}

export function loadChatMarkdown() {
  const html = fs.readFileSync(INDEX_HTML_PATH, "utf8");
  const source = extractMarkdownRenderScript(html);
  const fakeDoc = makeFakeDocument();
  const sandbox = { window: {}, document: fakeDoc, URL };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "index.html#markdown-render" });
  const api = sandbox.window.ChatMarkdown;
  if (!api || typeof api.parseMarkdown !== "function") {
    throw new Error("window.ChatMarkdown.parseMarkdown was not set by the extracted script block");
  }
  return {
    parseMarkdown: api.parseMarkdown,
    buildFragment: api.buildFragment,
    renderMarkdown: api.renderMarkdown,
    document: fakeDoc,
  };
}
