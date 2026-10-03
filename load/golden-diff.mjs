#!/usr/bin/env node
// Golden diff: the same GET requests against two gateways, responses compared
// field by field. Proves a backend change (SLO-49: supabase-js → direct pg) did not
// move the API contract — status codes, JSON shapes, value TYPES.
//
//   node load/golden-diff.mjs --a https://sloco.pp.ua --b http://127.0.0.1:3000 \
//     --requests load/golden/feed-places.txt [--bearer-env SLOCO_BEARER] \
//     [--ignore key1,key2] [--no-default-ignore] [--timeout 30000] [--self-check]
//
// Requests file: one request per line, `GET /v1/...`; blank lines and `# comments`
// are skipped. A leading `AUTH` sends `Authorization: Bearer $<bearer-env>`
// (default env var SLOCO_BEARER). Only GET/HEAD — this never writes.
//
// Comparison is deep and type-strict: 4.5 vs "4.5" is a difference, a missing key
// vs null is a difference; key order is ignored, array order is not. Non-JSON bodies
// (MVT tiles) compare by length + sha256. Exit code 1 if any request differs.
//
// --ignore drops keys at any depth, on top of the defaults (volatile per call):
//   generatedAt, expiresAt — feed meta, stamped at serve time
//   requestId              — feed meta, recommender snapshot id (new per snapshot)
//   cacheStatus            — feed meta, hit/miss depends on each gateway's own cache
// Timestamps that come from the DB (savedAt, createdAt, updatedAt, lastViewedAt) are
// NOT ignored on purpose: their string format is part of what this checks.
//
// --self-check first fetches A twice per request and lists the paths that differ
// between two identical calls (ranking ties, clocks). Those paths are then shown as
// `flaky` in the A-vs-B pass and do not fail the run.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

const DEFAULT_IGNORE = ["generatedAt", "expiresAt", "requestId", "cacheStatus"];
const MAX_PATHS_SHOWN = 20;
const MAX_VALUE_CHARS = 80;

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i === -1 ? fallback : args[i + 1];
};
const A = opt("--a")?.replace(/\/$/, "");
const B = opt("--b")?.replace(/\/$/, "");
const requestsFile = opt("--requests");
if (!A || !B || !requestsFile) {
  console.error(
    "usage: node load/golden-diff.mjs --a <base-url> --b <base-url> --requests <file> " +
      "[--bearer-env SLOCO_BEARER] [--ignore k1,k2] [--no-default-ignore] [--timeout ms] [--self-check]"
  );
  process.exit(2);
}
const bearerEnv = opt("--bearer-env", "SLOCO_BEARER");
const timeoutMs = Number(opt("--timeout", "30000"));
const selfCheck = args.includes("--self-check");
const ignore = new Set([
  ...(args.includes("--no-default-ignore") ? [] : DEFAULT_IGNORE),
  ...(opt("--ignore", "") || "").split(",").map((k) => k.trim()).filter(Boolean)
]);

// --- requests file ----------------------------------------------------------

const requests = [];
readFileSync(requestsFile, "utf8").split("\n").forEach((raw, idx) => {
  const line = raw.trim();
  if (!line || line.startsWith("#")) return;
  const parts = line.split(/\s+/);
  const auth = parts[0] === "AUTH";
  if (auth) parts.shift();
  const [method, path] = parts;
  if (!["GET", "HEAD"].includes(method) || !path?.startsWith("/")) {
    console.error(`${requestsFile}:${idx + 1}: expected "[AUTH] GET /path", got: ${line}`);
    process.exit(2);
  }
  requests.push({ auth, method, path, label: `${auth ? "AUTH " : ""}${method} ${path}` });
});
const token = process.env[bearerEnv];
if (requests.some((r) => r.auth) && !token) {
  console.error(`AUTH requests in ${requestsFile} but $${bearerEnv} is empty`);
  process.exit(2);
}

// --- fetch ------------------------------------------------------------------

async function call(base, req) {
  const started = performance.now();
  try {
    const res = await fetch(base + req.path, {
      method: req.method,
      headers: {
        accept: "application/json",
        ...(req.auth ? { authorization: `Bearer ${token}` } : {})
      },
      signal: AbortSignal.timeout(timeoutMs)
    });
    const buf = Buffer.from(await res.arrayBuffer());
    const ms = Math.round(performance.now() - started);
    const type = res.headers.get("content-type") ?? "";
    if (type.includes("json")) {
      try {
        return { status: res.status, ms, body: JSON.parse(buf.toString("utf8")) };
      } catch {
        // fall through: compare as bytes
      }
    }
    return {
      status: res.status,
      ms,
      body: { $binary: { contentType: type, length: buf.length, sha256: createHash("sha256").update(buf).digest("hex") } }
    };
  } catch (err) {
    const ms = Math.round(performance.now() - started);
    return { status: "ERR", ms, body: { $error: err.name === "TimeoutError" ? `timeout ${timeoutMs} ms` : String(err.message ?? err) } };
  }
}

// --- diff -------------------------------------------------------------------

const kind = (v) => (v === null ? "null" : Array.isArray(v) ? "array" : typeof v);
const MISSING = Symbol("missing");

function diff(a, b, path, out) {
  const ka = a === MISSING ? "missing" : kind(a);
  const kb = b === MISSING ? "missing" : kind(b);
  if (ka !== kb) {
    out.push({ path, a, b });
    return;
  }
  if (ka === "array") {
    const n = Math.max(a.length, b.length);
    for (let i = 0; i < n; i++) {
      diff(i < a.length ? a[i] : MISSING, i < b.length ? b[i] : MISSING, `${path}[${i}]`, out);
    }
    return;
  }
  if (ka === "object") {
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of [...keys].sort()) {
      if (ignore.has(k)) continue;
      const sub = /^[A-Za-z_$][\w$]*$/.test(k) ? `${path}.${k}` : `${path}[${JSON.stringify(k)}]`;
      diff(Object.hasOwn(a, k) ? a[k] : MISSING, Object.hasOwn(b, k) ? b[k] : MISSING, sub, out);
    }
    return;
  }
  if (!Object.is(a, b)) out.push({ path, a, b });
}

function compare(ra, rb) {
  const out = [];
  if (ra.status !== rb.status) out.push({ path: "status", a: ra.status, b: rb.status });
  diff(ra.body, rb.body, "$", out);
  return out;
}

const show = (v) => {
  if (v === MISSING) return "<missing>";
  const s = JSON.stringify(v) ?? String(v);
  return s.length > MAX_VALUE_CHARS ? `${s.slice(0, MAX_VALUE_CHARS)}… (${s.length} chars)` : s;
};
const typeHint = (a, b) =>
  a !== MISSING && b !== MISSING && kind(a) !== kind(b) ? `  [${kind(a)} vs ${kind(b)}]` : "";
// `$.places[3].rating` and `$.places[7].rating` are the same field for the summary.
const fieldOf = (path) => path.replace(/\[\d+\]/g, "[]");

// --- run --------------------------------------------------------------------

const flakyByRequest = new Map();

if (selfCheck) {
  console.log(`## Self-check: A twice (${A}), ${requests.length} requests\n`);
  const fields = new Map();
  for (const req of requests) {
    const first = await call(A, req);
    const second = await call(A, req);
    const paths = compare(first, second).map((d) => d.path);
    flakyByRequest.set(req.label, new Set(paths));
    console.log(`${paths.length ? "FLAKY" : "same "}  ${first.status}  ${req.label}${paths.length ? `  (${paths.length} paths)` : ""}`);
    for (const p of paths) fields.set(fieldOf(p), (fields.get(fieldOf(p)) ?? 0) + 1);
  }
  if (fields.size) {
    console.log("\nnondeterministic on A (field: occurrences) — consider --ignore for keys that are pure noise:");
    for (const [f, n] of [...fields].sort((x, y) => y[1] - x[1])) console.log(`  ${f}: ${n}`);
  }
  console.log("");
}

console.log(`## A ${A}  vs  B ${B}, ${requests.length} requests, ignoring: ${[...ignore].join(", ") || "nothing"}\n`);
let failed = 0;
let flakyOnly = 0;
for (const req of requests) {
  const ra = await call(A, req);
  const rb = await call(B, req);
  const flaky = flakyByRequest.get(req.label) ?? new Set();
  const diffs = compare(ra, rb);
  const real = diffs.filter((d) => !flaky.has(d.path));
  const verdict = real.length ? "DIFF " : diffs.length ? "flaky" : "OK   ";
  if (real.length) failed++;
  else if (diffs.length) flakyOnly++;
  console.log(
    `${verdict}  ${ra.status} ${rb.status}  ${req.label}  (A ${ra.ms} ms, B ${rb.ms} ms)` +
      (diffs.length ? `  ${real.length} diff${flaky.size ? `, ${diffs.length - real.length} flaky` : ""}` : "")
  );
  for (const d of diffs.slice(0, MAX_PATHS_SHOWN)) {
    const mark = flaky.has(d.path) ? "~" : "-";
    console.log(`    ${mark} ${d.path}: A ${show(d.a)} | B ${show(d.b)}${typeHint(d.a, d.b)}`);
  }
  if (diffs.length > MAX_PATHS_SHOWN) console.log(`    … ${diffs.length - MAX_PATHS_SHOWN} more`);
}

console.log(`\n${requests.length - failed - flakyOnly} same, ${flakyOnly} flaky-only, ${failed} different`);
process.exit(failed ? 1 : 0);
