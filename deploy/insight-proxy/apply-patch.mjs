#!/usr/bin/env node
// Wire the site's analyst work into insight-proxy (the Worker the live site's AI calls), in place, idempotently.
//
//   node deploy/insight-proxy/apply-patch.mjs <path to the insight-proxy checkout>
//   node deploy/insight-proxy/apply-patch.mjs --check <path>      reports what is applied, writes nothing
//
// What it does, each step found by an anchor that must occur exactly once across the proxy's src/*.js (a step whose
// anchor is missing or ambiguous stops the run before any file is written, naming the step):
//   1. copies docs/ai-analyst/analyst-endpoint.mjs to src/analyst.js and step-schema.json beside it;
//   2. routes /analyst to analystFetch at the top of the entry fetch (before the proxy's own path whitelist);
//   3. validateResults keeps results.analyst_insights (it is a whitelist: today the list is silently dropped);
//   4. the figure guard (buildIndex) indexes those sentences, so a figure the report copies from one is grounded;
//   5. "analyst_insights" joins INTERNAL_NAMES (a report that prints the field name is caught like the others);
//   6. the report prompt says how to use them: descriptive, never graded, figures copied as written.
// Then: npm test (or the proxy's own checks) and npx wrangler deploy, from the proxy checkout, with its wrangler.toml.
import { readFileSync, writeFileSync, readdirSync, copyFileSync, existsSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const site = resolve(here, "..", "..");
const args = process.argv.slice(2);
const check = args[0] === "--check";
const target = resolve(check ? args[1] || "" : args[0] || "");
// a directory is a proxy checkout (src/*.js); a single file is a bundle (the test harness patches the deployed one)
const bundle = existsSync(target) && statSync(target).isFile();
if (!args.length || !existsSync(target)) {
  console.error("usage: node deploy/insight-proxy/apply-patch.mjs [--check] <insight-proxy checkout>");
  process.exit(2);
}
const srcDir = bundle ? dirname(target) : join(target, "src");
const files = bundle ? [target] : readdirSync(srcDir).filter((f) => f.endsWith(".js") && f !== "analyst.js").map((f) => join(srcDir, f));
const text = new Map(files.map((f) => [f, readFileSync(f, "utf8")]));

const MARK = "northledger-analyst";
const HOOK = `if (new URL(request.url).pathname === "/analyst") return analystFetch(request, env || {}); // ${MARK}`;
const PROMPT_LINES = [
  "  analyst_insights (when present): what the page's analyst loop found, each a plain sentence; the first may be",
  '    "Key numbers: ...". They are descriptive, never tested: never call one significant, proven or a driver, never',
  '    give one a grade, and copy each figure exactly as written. Use them under "## Other findings" (or under',
  '    "## What drove it" only beside a graded finding that already says so).',
]
  .map((l, i, a) => "    " + JSON.stringify(l) + "," + (i === a.length - 1 ? " // " + MARK : ""))
  .join("\n");

const steps = [
  {
    name: "route /analyst to analystFetch in the entry fetch",
    done: (t) => t.includes(HOOK),
    // the entry's fetch takes (request, env, ...); the Durable Object's fetch(request) does not match
    re: /(\n[ \t]*async fetch\(request, env(?:, ctx)?\) \{\n)/,
    edit: (t, m) => t.replace(m[1], m[1] + "    " + HOOK + "\n"),
    imports: true,
  },
  {
    name: "validateResults keeps analyst_insights",
    done: (t) => t.includes("analyst_insights: strList(r.analyst_insights"),
    re: /(\n([ \t]*)limitations: strList\(r\.limitations, 240, 6\))(\n)/,
    edit: (t, m) => t.replace(m[0], m[1] + `,\n${m[2]}// the analyst loop's sentences (${MARK})\n${m[2]}analyst_insights: strList(r.analyst_insights, 400, 10)` + m[3]),
  },
  {
    name: "the figure guard indexes analyst_insights",
    done: (t) => t.includes("addList(r.analyst_insights)"),
    re: /(\n([ \t]*)addList\(r\.analyses_refused\);\n)/,
    edit: (t, m) => t.replace(m[1], m[1] + `${m[2]}addList(r.analyst_insights); // ${MARK}\n`),
  },
  {
    name: "INTERNAL_NAMES knows analyst_insights",
    done: (t) => /INTERNAL_NAMES[^\]]*"analyst_insights"/.test(t),
    re: /(INTERNAL_NAMES\s*=\s*(?:\/\*[^*]*\*\/\s*)?new Set\(\[\n?)/,
    edit: (t, m) => t.replace(m[1], m[1] + `  "analyst_insights",\n`),
  },
  {
    name: "the report prompt says how to use analyst_insights",
    done: (t) => t.includes(`(when present): what the page's analyst loop found`),
    re: /(\n[ \t]*'  "## Other findings: <in words>" \(optional\): the other claims and analyses a busy reader needs\.',\n)/,
    edit: (t, m) => t.replace(m[1], m[1] + PROMPT_LINES + "\n"),
  },
];

const plan = [];
let failed = false;
for (const s of steps) {
  const already = [...text.values()].some((t) => s.done(t));
  if (already) {
    plan.push([s, null, "applied"]);
    continue;
  }
  const hits = [...text.entries()].flatMap(([f, t]) => {
    const n = (t.match(new RegExp(s.re.source, "g")) || []).length;
    return n ? [[f, n]] : [];
  });
  const total = hits.reduce((a, [, n]) => a + n, 0);
  if (total !== 1) {
    failed = true;
    plan.push([s, null, total ? `anchor found ${total} times (${hits.map(([f]) => f).join(", ")})` : "anchor not found"]);
  } else plan.push([s, hits[0][0], "to apply"]);
}
for (const [s, f, state] of plan) console.log(`${state === "applied" ? "ok  " : state === "to apply" ? "todo" : "FAIL"}  ${s.name}${f ? "  (" + f + ")" : ""}${state !== "applied" && state !== "to apply" ? ": " + state : ""}`);
if (failed) {
  console.error("\nNothing written. The proxy's source differs from the version this patch was made against; apply the failing step by hand (deploy/insight-proxy/README.md says what each one is).");
  process.exit(1);
}
if (check) process.exit(plan.every(([, , st]) => st === "applied") ? 0 : 1);

for (const [s, f, state] of plan) {
  if (state !== "to apply") continue;
  let t = text.get(f);
  t = s.edit(t, t.match(s.re));
  if (s.imports && !t.includes('from "./analyst.js"')) t = `import { analystFetch } from "./analyst.js"; // ${MARK}\n` + t;
  text.set(f, t);
}
copyFileSync(join(site, "docs/ai-analyst/analyst-endpoint.mjs"), join(srcDir, "analyst.js"));
copyFileSync(join(site, "docs/ai-analyst/step-schema.json"), join(srcDir, "step-schema.json"));
for (const [f, t] of text) writeFileSync(f, t);
console.log(`\nWritten. Copied src/analyst.js and src/step-schema.json. Next, in ${bundle ? srcDir : target}: run the proxy's tests, then npx wrangler deploy.`);
