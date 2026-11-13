import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

// Source guardrails from the Lovable guide (_docs/planning/lovable-guidance.md) held as a test.
// The scan covers every file under frontend/src except src/test/ (the tests name the patterns).
//
// Known limits: this is a text scan, so it cannot see these ways round a rule:
// - a computed property name, e.g. {...{ ["dangerouslySet" + "InnerHTML"]: { __html: h } }};
// - a function passed around under another name: `const g = fetch;`,
//   `const s = Array.prototype.sort;`, or a destructured `const { sort } = ...`;
// - a string built at run time;
// - `srcDoc` on an iframe;
// - the packages `sanitize-html` and `htmlparser2`;
// - a comment placed between the name and the bracket: `fetch/**/(u)`, `a.sort/**/(c)`;
// - optional chaining on `document`: `document?.write(t)`;
// - an import path that reaches `src/test/` through another segment first: `@/components/../test/x`.
// The bracket rules also refuse a one-element array of that word (`["sort"]`, `["fetch"]`).
// They wait for lint rules (#31).

const MOCK = "services/mockService.ts";
const HTTP = "services/http.ts";

// Locate frontend/src without import.meta.url: under Vitest's jsdom environment `new URL(...)` is
// jsdom's URL and `fileURLToPath` rejects it ("The URL must be of scheme file", CI run 42967613566).
// Vitest's root is frontend/ (`cd frontend && bun run test`), so `process.cwd()` is frontend/. The
// repo-root candidate keeps the test working if it is started from the repo root. A directory only
// counts if it holds the mock, so a wrong guess fails loudly instead of scanning nothing.
function findSrc(): string {
  const candidates = [resolve(process.cwd(), "src"), resolve(process.cwd(), "frontend", "src")];
  const found = candidates.find((dir) => existsSync(join(dir, MOCK)));
  if (!found) throw new Error(`guardrails: frontend/src not found; looked in ${candidates.join(", ")}`);
  return found;
}

const SRC = findSrc();

// The only `<script` text allowed anywhere: the hostile fixture title in the mock.
const SCRIPT_FIXTURE = "<script>alert('xss')</script> in alert title";

interface SourceFile {
  path: string; // relative to src/, with forward slashes
  text: string;
}

interface Rule {
  name: string;
  pattern: RegExp;
  allowedIn?: string; // the one file where the pattern may appear
}

const MARKDOWN_PACKAGES = "markdown|marked|linkify|remark|rehype|showdown|micromark|snarkdown|mdast";

const HTML_PACKAGES = "html-react-parser|html-to-react|react-html-parser|dompurify";

// The import shapes shared by the package rules: `from "x"`, `import("x")`, `import "x"`, `require("x")`.
const importOf = (inner: string) =>
  new RegExp(`(?:from\\s*|import\\s*\\(?\\s*|require\\(\\s*)["'][^"']*${inner}[^"']*["']`, "i");

const RULES: Rule[] = [
  { name: "dangerouslySetInnerHTML", pattern: /dangerouslySetInnerHTML/ },
  { name: "innerHTML", pattern: /innerHTML/ },
  { name: "HTML sink", pattern: /insertAdjacentHTML|outerHTML|document\s*\.\s*write|createContextualFragment|setHTMLUnsafe|parseHTMLUnsafe/ },
  { name: "HTML-parsing package import", pattern: importOf(`(?:${HTML_PACKAGES})`) },
  { name: "XMLHttpRequest or sendBeacon", pattern: /XMLHttpRequest|sendBeacon/ },
  {
    name: "markdown or linkify import",
    pattern: importOf(`(?:${MARKDOWN_PACKAGES})`),
  },
  { name: "third-party service name", pattern: /supabase|gtag|posthog|sentry|analytics/i },
  { name: "WebSocket or EventSource", pattern: /WebSocket|EventSource/ },
  {
    name: "fetch( outside http.ts",
    pattern: /fetch\s*\(|\[\s*["'`]fetch["'`]\s*\]|\b(?:window|globalThis|self)\s*\??\.\s*fetch\b/,
    allowedIn: HTTP,
  },
  {
    name: ".sort( or .toSorted( outside mockService.ts",
    pattern: /\.\s*(?:sort|toSorted)\s*\(|\[\s*["'`](?:sort|toSorted)["'`]\s*\]/,
    allowedIn: MOCK,
  },
];

// An import specifier that reaches into src/test/: `@/test`, `@/test/...` or a relative path with a `test` segment.
function importsFromTestFolder(text: string): boolean {
  const specifiers = text.matchAll(/(?:from\s*|import\s*\(?\s*|require\(\s*)["']([^"']*)["']/g);
  return [...specifiers].some(([, spec]) => /^@\/test(?:\/|$)/.test(spec) || (spec.startsWith(".") && spec.split("/").includes("test")));
}

/** Files outside src/test/ must not import from it. */
function findTestImports(files: SourceFile[]): string[] {
  return files.filter((f) => importsFromTestFolder(f.text)).map((f) => `${f.path}: import from src/test`);
}

/** Paths relative to src/ (e.g. "test/helper.tsx"); only `*.test.ts(x)` and `setup.ts` may sit directly in src/test/. */
function findStrayTestFiles(paths: string[]): string[] {
  return paths
    .filter((p) => !/^test\/[^/]+\.test\.tsx?$/.test(p) && p !== "test/setup.ts")
    .map((p) => `${p}: not a test file or setup.ts`);
}

/** Returns one line per rule broken, as "<file>: <rule>". */
function findViolations(files: SourceFile[]): string[] {
  const found: string[] = [];
  for (const file of files) {
    for (const rule of RULES) {
      if (rule.allowedIn === file.path) continue;
      if (rule.pattern.test(file.text)) found.push(`${file.path}: ${rule.name}`);
    }
    // `<script` is allowed only as the exact fixture text in the mock.
    const text = file.path === MOCK ? file.text.split(SCRIPT_FIXTURE).join("") : file.text;
    if (/<script/i.test(text)) found.push(`${file.path}: <script`);
  }
  return found;
}

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

function readSource(): SourceFile[] {
  return walk(SRC)
    .map((full) => ({ full, path: relative(SRC, full).split(sep).join("/") }))
    .filter((f) => !f.path.startsWith("test/"))
    .map((f) => ({ path: f.path, text: readFileSync(f.full, "utf8") }));
}

describe("source guardrails", () => {
  it("scans the real source tree and finds none of the banned patterns", () => {
    const files = readSource();

    // Guard against a scan that silently reads nothing.
    expect(files.length).toBeGreaterThan(20);
    expect(files.map((f) => f.path)).toContain(MOCK);
    expect(files.map((f) => f.path)).toContain(HTTP);
    expect(files.some((f) => f.path.startsWith("test/"))).toBe(false);

    expect(findViolations(files)).toEqual([]);
  });

  it("finds no import from src/test/ in the real source tree", () => {
    expect(findTestImports(readSource())).toEqual([]);
  });

  it("holds only test files and setup.ts in the real src/test/ folder", () => {
    const inTest = walk(SRC)
      .map((full) => relative(SRC, full).split(sep).join("/"))
      .filter((p) => p.startsWith("test/"));

    expect(inTest).toContain("test/setup.ts");
    expect(inTest).toContain("test/guardrails.test.ts");
    expect(findStrayTestFiles(inTest)).toEqual([]);
  });

  it("keeps the hostile fixture in the mock, as the only <script text", () => {
    const mock = readSource().find((f) => f.path === MOCK);

    expect(mock?.text).toContain(SCRIPT_FIXTURE);
    expect(findViolations(mock ? [mock] : [])).toEqual([]);
  });

  describe("the check refuses", () => {
    const component = (text: string): SourceFile[] => [{ path: "components/soc/Evil.tsx", text }];

    it("dangerouslySetInnerHTML added to a component", () => {
      const files = component('<div dangerouslySetInnerHTML={{ __html: note.text }} />');

      // CI run 42967972731 (round 2): this test also expected "components/soc/Evil.tsx: innerHTML"
      // (the removed line was `expect(found).toContain("components/soc/Evil.tsx: innerHTML")`).
      // That expectation was wrong: the scanner's innerHTML rule is /innerHTML/, which is case
      // sensitive, and `dangerouslySetInnerHTML` spells it `InnerHTML` (capital I), so the two
      // rules do not overlap. The issue lists the two strings separately and `innerHTML` is the
      // DOM property name, which is case sensitive in JS (`innerHtml` is not a banned API), so
      // the scanner is kept as it is: making it case-insensitive would only add false positives.
      // The refusal is not lost: the exact list proves the input is reported once, as
      // dangerouslySetInnerHTML and nothing else. The separate innerHTML row below proves
      // `el.innerHTML = text;` is reported as innerHTML.
      expect(findViolations(files)).toEqual(["components/soc/Evil.tsx: dangerouslySetInnerHTML"]);
    });

    // Each row names the one rule it must trigger, so a row cannot pass because a different rule
    // matched (round 2 tightened these from "not empty" to the exact list; none was loosened).
    const PKG_IMPORT = "markdown or linkify import";
    const THIRD_PARTY = "third-party service name";
    it.each([
      ["innerHTML", "el.innerHTML = text;", "innerHTML"],
      ["markdown import", 'import { marked } from "marked";', PKG_IMPORT],
      ["react-markdown import", 'import Markdown from "react-markdown";', PKG_IMPORT],
      ["linkify import", 'import Linkify from "linkify-react";', PKG_IMPORT],
      ["supabase", 'import { createClient } from "@supabase/supabase-js";', THIRD_PARTY],
      ["gtag", 'window.gtag("event", "x");', THIRD_PARTY],
      ["posthog", 'import posthog from "posthog-js";', THIRD_PARTY],
      ["sentry", 'import * as Sentry from "@sentry/react";', THIRD_PARTY],
      ["analytics", "const analyticsId = 1;", THIRD_PARTY],
      ["WebSocket", 'new WebSocket("ws://x");', "WebSocket or EventSource"],
      ["EventSource", 'new EventSource("/stream");', "WebSocket or EventSource"],
      ["fetch outside http.ts", 'await fetch("/api/queue");', "fetch( outside http.ts"],
      [".sort(", "cases.sort((a, b) => a.id.localeCompare(b.id));", ".sort( or .toSorted( outside mockService.ts"],
      [".toSorted(", "const s = cases.toSorted();", ".sort( or .toSorted( outside mockService.ts"],
      ["<script", 'const s = "<script>alert(1)</script>";', "<script"],
    ])("%s", (_label, text, rule) => {
      expect(findViolations(component(text))).toEqual([`components/soc/Evil.tsx: ${rule}`]);
    });

    const FETCH_RULE = "fetch( outside http.ts";
    const SORT_RULE = ".sort( or .toSorted( outside mockService.ts";
    it.each([
      ["fetch with a space (input 1)", 'fetch ("/api/queue")', FETCH_RULE],
      ["fetch with a line break", 'fetch\n("/api/queue")', FETCH_RULE],
      [".sort with a space", "[3, 1].sort (cmp)", SORT_RULE],
      [".toSorted with a line break", "list.toSorted\n(cmp)", SORT_RULE],
      ['["sort"] (input 7)', 'a["sort"]()', SORT_RULE],
      ["['toSorted']", "a['toSorted']()", SORT_RULE],
      ["[`sort`] after ?.", "a?.[ `sort` ]()", SORT_RULE],
      ['window["fetch"]', 'window["fetch"]("/x")', FETCH_RULE],
      ["window.fetch alias (input 2)", 'const g = window.fetch; g("/api/queue")', FETCH_RULE],
      ["window?.fetch alias", "const g = window?.fetch;", FETCH_RULE],
      ["globalThis.fetch","const g = globalThis.fetch;", FETCH_RULE],
      ["self.fetch", "const g = self.fetch;", FETCH_RULE],
      ["insertAdjacentHTML (input 3)", 'el.insertAdjacentHTML("beforeend", t)', "HTML sink"],
      ["outerHTML (input 3)", "el.outerHTML = t", "HTML sink"],
      ["input 3, both lines", 'el.insertAdjacentHTML("beforeend", t);\nel.outerHTML = t;', "HTML sink"],
      ["document.write", "document.write(t);", "HTML sink"],
      ["document . write", "document . write(t);", "HTML sink"],
      ["createContextualFragment", "range.createContextualFragment(t);", "HTML sink"],
      ["setHTMLUnsafe", "el.setHTMLUnsafe(t);", "HTML sink"],
      ["parseHTMLUnsafe", "const d = Document.parseHTMLUnsafe(t);", "HTML sink"],
      ["new XMLHttpRequest() (input 4)", "const x = new XMLHttpRequest();", "XMLHttpRequest or sendBeacon"],
      ['navigator.sendBeacon (input 4)', 'navigator.sendBeacon("/x");', "XMLHttpRequest or sendBeacon"],
      ["html-react-parser import (input 8)", 'import parse from "html-react-parser";', "HTML-parsing package import"],
      ["html-to-react import", 'import { Parser } from "html-to-react";', "HTML-parsing package import"],
      ["react-html-parser import", 'import ReactHtmlParser from "react-html-parser";', "HTML-parsing package import"],
      ["dompurify import", 'import DOMPurify from "dompurify";', "HTML-parsing package import"],
      ["dompurify dynamic import", 'const p = await import("dompurify");', "HTML-parsing package import"],
      ["dompurify require", 'const p = require("dompurify");', "HTML-parsing package import"],
    ])("%s", (_label, text, rule) => {
      expect(findViolations(component(text))).toEqual([`components/soc/Evil.tsx: ${rule}`]);
    });

    it("XMLHttpRequest and sendBeacon even in http.ts", () => {
      const files: SourceFile[] = [{ path: HTTP, text: "new XMLHttpRequest(); navigator.sendBeacon(u);" }];

      expect(findViolations(files)).toEqual([`${HTTP}: XMLHttpRequest or sendBeacon`]);
    });

    it("myself.fetch is not read as self.fetch", () => {
      expect(findViolations(component("myself.fetch"))).toEqual([]);
    });

    it("an import from @/test or a relative test folder", () => {
      const imports = ['import { h } from "@/test/helper";', 'import { h } from "../../test/helper";'];

      for (const text of imports) {
        expect(findTestImports(component(text))).toEqual(["components/soc/Evil.tsx: import from src/test"]);
      }
      expect(findTestImports(component('import "@/test";'))).toEqual(["components/soc/Evil.tsx: import from src/test"]);
      expect(findTestImports(component('import { h } from "../test/x";'))).toEqual([
        "components/soc/Evil.tsx: import from src/test",
      ]);
      expect(findTestImports(component('import { h } from "./latest";'))).toEqual([]);
      expect(findTestImports(component('import test from "node:test";'))).toEqual([]);
    });

    it("a file in src/test/ that is not a test file or setup.ts", () => {
      expect(findStrayTestFiles(["test/helper.tsx"])).toEqual(["test/helper.tsx: not a test file or setup.ts"]);
      expect(findStrayTestFiles(["test/fixtures/data.ts"])).toEqual([
        "test/fixtures/data.ts: not a test file or setup.ts",
      ]);
      expect(findStrayTestFiles(["test/a.test.ts", "test/b.test.tsx", "test/setup.ts"])).toEqual([]);
    });

    it("a <script text in the mock that is not the fixture", () => {
      const files: SourceFile[] = [
        { path: MOCK, text: `${SCRIPT_FIXTURE}\nconst x = "<script>alert(2)</script>";` },
      ];

      expect(findViolations(files)).toEqual([`${MOCK}: <script`]);
    });

    it("fetch( in a service file other than http.ts", () => {
      const files: SourceFile[] = [{ path: "services/httpService.ts", text: 'fetch("/api/x")' }];

      expect(findViolations(files)).toEqual(["services/httpService.ts: fetch( outside http.ts"]);
    });
  });

  describe("the check allows", () => {
    it("fetch( in http.ts and .sort( in mockService.ts", () => {
      const files: SourceFile[] = [
        { path: HTTP, text: "res = await fetch(url);" },
        { path: MOCK, text: "list.sort((a, b) => a - b);" },
      ];

      expect(findViolations(files)).toEqual([]);
    });

    it("fetch (url) in http.ts and list.sort (cmp) in mockService.ts", () => {
      const files: SourceFile[] = [
        { path: HTTP, text: "res = await fetch (url);" },
        { path: MOCK, text: "list.sort (cmp);" },
      ];

      expect(findViolations(files)).toEqual([]);
    });
  });
});
