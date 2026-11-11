import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

// Source guardrails from the Lovable guide (_docs/planning/lovable-guidance.md) held as a test.
// The scan covers every file under frontend/src except src/test/ (the tests name the patterns).

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

const RULES: Rule[] = [
  { name: "dangerouslySetInnerHTML", pattern: /dangerouslySetInnerHTML/ },
  { name: "innerHTML", pattern: /innerHTML/ },
  {
    name: "markdown or linkify import",
    pattern: new RegExp(
      `(?:from\\s*|import\\s*\\(?\\s*|require\\(\\s*)["'][^"']*(?:${MARKDOWN_PACKAGES})[^"']*["']`,
      "i",
    ),
  },
  { name: "third-party service name", pattern: /supabase|gtag|posthog|sentry|analytics/i },
  { name: "WebSocket or EventSource", pattern: /WebSocket|EventSource/ },
  { name: "fetch( outside http.ts", pattern: /fetch\(/, allowedIn: HTTP },
  { name: ".sort( or .toSorted( outside mockService.ts", pattern: /\.sort\(|\.toSorted\(/, allowedIn: MOCK },
];

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
  });
});
