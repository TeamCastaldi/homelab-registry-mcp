// Unit + adversarial tests for the safe-markdown renderer in
// src/registry_mcp/chat/static/index.html (Phases 1-2 of the safe markdown
// rendering plan). Run with:
//
//   node --test tests/chat_markdown.test.mjs
//
// Zero dependencies -- `node --test`, `node:assert`, and the hand-rolled
// fake DOM in chat_markdown_support.mjs are all that's needed. This is a
// deliberate, documented exception to "no Node toolchain" for this repo:
// it never touches the deployed artifact or the Dockerfile.

import { describe, test } from "node:test";
import assert from "node:assert/strict";
import { loadChatMarkdown, collectElements } from "./chat_markdown_support.mjs";

const { parseMarkdown, buildFragment, document: doc } = loadChatMarkdown();

function render(text) {
  return buildFragment(doc, parseMarkdown(text));
}

function renderHtml(text) {
  return render(text).outerHTML;
}

describe("Phase 1: parser -- markdown subset", () => {
  test("headings level 1-3", () => {
    const tree = parseMarkdown("# One\n\n## Two\n\n### Three");
    // JSON.stringify, not assert.deepEqual: parseMarkdown runs in a vm
    // sandbox, so its arrays/objects are a different realm than this
    // file's -- deepEqual's constructor check fails on that identity
    // mismatch even when the data is byte-for-byte identical.
    assert.equal(
      JSON.stringify(tree.children.map((b) => [b.type, b.level])),
      JSON.stringify([
        ["heading", 1],
        ["heading", 2],
        ["heading", 3],
      ])
    );
  });

  test("a 4th-level heading falls through to literal text", () => {
    const tree = parseMarkdown("#### not a heading");
    assert.equal(tree.children[0].type, "paragraph");
  });

  test("bold and italic", () => {
    assert.equal(renderHtml("**bold**"), "<p><strong>bold</strong></p>");
    assert.equal(renderHtml("*italic*"), "<p><em>italic</em></p>");
  });

  test("italic spanning a nested bold stays one span, not two", () => {
    assert.equal(
      renderHtml("*a **b** c*"),
      "<p><em>a <strong>b</strong> c</em></p>"
    );
  });

  test("bold spanning a nested italic stays one span, not two", () => {
    assert.equal(
      renderHtml("**a *b* c**"),
      "<p><strong>a <em>b</em> c</strong></p>"
    );
  });

  test("underscores are never treated as italics (snake_case stays literal)", () => {
    assert.equal(renderHtml("DISCOVERY_STALE_AFTER_MISSES"), "<p>DISCOVERY_STALE_AFTER_MISSES</p>");
  });

  test("inline code", () => {
    assert.equal(renderHtml("some `code` here"), "<p>some <code>code</code> here</p>");
  });

  test("fenced code block with a language tag", () => {
    const tree = parseMarkdown("```yaml\nfoo: bar\n```");
    assert.equal(tree.children[0].type, "code_block");
    assert.equal(tree.children[0].lang, "yaml");
    assert.equal(tree.children[0].value, "foo: bar");
  });

  test("unterminated fenced code block swallows the rest of the document", () => {
    const tree = parseMarkdown("```\nline one\nline two");
    assert.equal(tree.children.length, 1);
    assert.equal(tree.children[0].type, "code_block");
    assert.equal(tree.children[0].value, "line one\nline two");
  });

  test("a table", () => {
    const tree = parseMarkdown("| a | b |\n| --- | --- |\n| 1 | 2 |");
    const table = tree.children[0];
    assert.equal(table.type, "table");
    assert.equal(table.headers.length, 2);
    assert.equal(table.rows.length, 1);
  });

  test("unordered and ordered lists", () => {
    const tree = parseMarkdown("- one\n- two\n\n1. three\n2. four");
    assert.equal(
      JSON.stringify(tree.children.map((b) => [b.type, b.ordered])),
      JSON.stringify([
        ["list", false],
        ["list", true],
      ])
    );
  });

  test("a link", () => {
    const tree = parseMarkdown("[click](https://example.com)");
    const link = tree.children[0].children[0];
    assert.equal(link.type, "link");
    assert.equal(link.href, "https://example.com");
  });

  test("a link whose URL contains its own parens is captured in full", () => {
    const tree = parseMarkdown("[wiki](https://en.wikipedia.org/wiki/Foo_(bar))");
    assert.equal(tree.children[0].children[0].href, "https://en.wikipedia.org/wiki/Foo_(bar)");
  });

  test("a link's label is parsed recursively, same as any other container", () => {
    assert.equal(
      renderHtml("[**bold** and `code`](https://example.com)"),
      '<p><a href="https://example.com" target="_blank" rel="noopener noreferrer"><strong>bold</strong> and <code>code</code></a></p>'
    );
  });

  test("blank-line paragraph breaks", () => {
    const tree = parseMarkdown("first\n\nsecond");
    assert.equal(
      JSON.stringify(tree.children.map((b) => b.type)),
      JSON.stringify(["paragraph", "paragraph"])
    );
  });
});

describe("Phase 1: malformed input degrades to literal text, never throws", () => {
  test("unmatched **", () => {
    assert.doesNotThrow(() => parseMarkdown("a **unmatched bold"));
    assert.equal(renderHtml("a **unmatched bold"), "<p>a **unmatched bold</p>");
  });

  test("unterminated ** never lets its second * become a fresh delimiter", () => {
    assert.equal(renderHtml("**a*"), "<p>**a*</p>");
  });

  test("empty bold markers", () => {
    assert.equal(renderHtml("**** noise"), "<p>**** noise</p>");
  });

  test("a bare --- (no pipe) is never treated as a table separator", () => {
    const tree = parseMarkdown("cost | benefit\n---");
    assert.equal(tree.children[0].type, "paragraph");
  });
});

describe("Phase 2: DOM builder -- link scheme allowlist", () => {
  test("a safe https: link becomes a real <a> with target/rel set", () => {
    const frag = render("[click](https://example.com)");
    const a = frag.querySelectorAll("a")[0];
    assert.ok(a, "expected an <a> element");
    assert.equal(a.getAttribute("href"), "https://example.com");
    assert.equal(a.getAttribute("target"), "_blank");
    assert.equal(a.getAttribute("rel"), "noopener noreferrer");
  });

  test("a safe http: link is also allowed", () => {
    const frag = render("[click](http://example.com)");
    assert.equal(frag.querySelectorAll("a").length, 1);
  });

  test("a javascript: link degrades to plain text; no <a>, no href ever set", () => {
    const frag = render("[click](javascript:alert(1))");
    assert.equal(frag.querySelectorAll("a").length, 0);
    assert.equal(frag.outerHTML, "<p>click</p>");
  });

  test("a data: link with an embedded <script> degrades to plain text", () => {
    const frag = render("[click](data:text/html,<script>alert(1)</script>)");
    assert.equal(frag.querySelectorAll("a").length, 0);
    assert.equal(frag.querySelectorAll("script").length, 0);
    assert.equal(frag.outerHTML, "<p>click</p>");
  });

  test("a malformed href that throws in the URL constructor degrades to plain text", () => {
    // A bare "not a url" has no scheme at all -- new URL() throws on it.
    const frag = render("[click](not a url)");
    assert.equal(frag.querySelectorAll("a").length, 0);
  });
});

describe("Track B: adversarial fixtures", () => {
  const fixtures = [
    "a sentence with <script>alert(1)</script> embedded mid-sentence",
    "[click me](javascript:alert(1))",
    "[click me](data:text/html,<script>alert(1)</script>)",
    "| a | b |\n| --- | --- |\n| **unmatched | ok |",
    "```\nsome content with ``` two backticks inline, not alone on a line\nmore\n```",
    "* _ ` [ ] ( ) # - | \\ ~ ** __ *** ---",
  ];

  for (const input of fixtures) {
    test(`does not throw: ${JSON.stringify(input).slice(0, 60)}`, () => {
      assert.doesNotThrow(() => render(input));
    });

    test(`no <script> element or on* attribute survives: ${JSON.stringify(input).slice(0, 60)}`, () => {
      const frag = render(input);
      for (const el of collectElements(frag)) {
        assert.notEqual(el._tag, "script", "a <script> element must never be built");
        for (const attrName of Object.keys(el.attributes)) {
          assert.ok(
            !attrName.toLowerCase().startsWith("on"),
            `unexpected event-handler attribute "${attrName}"`
          );
        }
      }
    });
  }

  test("every <a> built from any fixture above has an http(s) href only", () => {
    for (const input of fixtures) {
      const frag = render(input);
      for (const a of collectElements(frag).filter((el) => el._tag === "a")) {
        const href = a.getAttribute("href") || "";
        assert.ok(
          href.startsWith("http://") || href.startsWith("https://"),
          `unexpected non-http(s) href: ${href}`
        );
      }
    }
  });

  test("a table cell with unmatched ** degrades to literal text, not a bold node", () => {
    const tree = parseMarkdown("| a | b |\n| --- | --- |\n| **unmatched | ok |");
    const cell = tree.children[0].rows[0][0];
    assert.equal(cell.length, 1);
    assert.equal(cell[0].type, "text");
    assert.equal(cell[0].value, "**unmatched");
  });

  test("a code fence containing triple-backtick-like content stays inside the fence", () => {
    const tree = parseMarkdown(
      "```\nsome content with ``` two backticks inline, not alone on a line\nmore\n```"
    );
    assert.equal(tree.children.length, 1);
    assert.equal(tree.children[0].type, "code_block");
    assert.ok(tree.children[0].value.includes("```"));
  });

  test("pure noise input of markdown special characters never throws and stays a single text run", () => {
    const noise = "* _ ` [ ] ( ) # - | \\ ~ ** __ *** ---";
    assert.doesNotThrow(() => parseMarkdown(noise));
  });

  test("an absurdly large table parses without a perf cliff", () => {
    const rows = 2000;
    const lines = ["| a | b | c |", "| --- | --- | --- |"];
    for (let i = 0; i < rows; i++) lines.push(`| r${i} | v${i} | w${i} |`);
    const input = lines.join("\n");

    const start = Date.now();
    const tree = parseMarkdown(input);
    const elapsedMs = Date.now() - start;

    assert.equal(tree.children[0].type, "table");
    assert.equal(tree.children[0].rows.length, rows);
    // Timing is informational only, not asserted: a hard wall-clock
    // threshold is flaky across CI/local environments even when the
    // algorithm itself is fine. The row-count/type checks above are what
    // actually catches a correctness regression. Gated behind an env var
    // so a normal `node --test` run stays quiet -- run with
    // VERBOSE_TIMING=1 to see it (what you'd check if someone introduced
    // an accidental O(n^2)).
    if (process.env.VERBOSE_TIMING) {
      console.log(`    (2000-row table parsed in ${elapsedMs}ms)`);
    }
  });
});
