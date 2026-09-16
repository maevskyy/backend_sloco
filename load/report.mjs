#!/usr/bin/env node
// Turns an Artillery JSON report (`artillery run --output x.json`) into the
// markdown we keep in docs/perf: overall numbers, one row per named request,
// and a 10-second timeline so the step at which latency or 5xx take off is
// visible. Usage: node report.mjs out/hot-paths.json [--title "..."]
import { readFileSync } from "node:fs";

const file = process.argv[2];
if (!file) {
  console.error("usage: node report.mjs <artillery-output.json> [--title text]");
  process.exit(1);
}
const titleIdx = process.argv.indexOf("--title");
const title = titleIdx === -1 ? "Load run" : process.argv[titleIdx + 1];

const data = JSON.parse(readFileSync(file, "utf8"));
const agg = data.aggregate;
const windows = data.intermediate ?? [];
const ENDPOINT_RT = "plugins.metrics-by-endpoint.response_time.";
const ENDPOINT_CODES = /^plugins\.metrics-by-endpoint\.(.+)\.codes\.(\d{3})$/;

const startedAt = agg.firstMetricAt ?? agg.firstCounterAt ?? windows[0]?.firstCounterAt;
const endedAt = agg.lastMetricAt ?? agg.lastCounterAt;
const durationS = Math.max(1, Math.round((endedAt - startedAt) / 1000));

const fmtMs = (v) => (v === undefined || v === null ? "—" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`);
const count = (counters, prefix) =>
  Object.entries(counters ?? {})
    .filter(([k]) => k.startsWith(prefix))
    .reduce((sum, [, v]) => sum + v, 0);
const codes = (counters, prefix = "http.codes.") => {
  const out = { "2xx": 0, "4xx": 0, "5xx": 0 };
  for (const [k, v] of Object.entries(counters ?? {})) {
    if (!k.startsWith(prefix)) continue;
    const code = k.slice(prefix.length);
    if (!/^\d{3}$/.test(code)) continue;
    out[`${code[0]}xx`] = (out[`${code[0]}xx`] ?? 0) + v;
  }
  return out;
};

const lines = [];
lines.push(`## ${title}`);
lines.push("");
lines.push(`${new Date(startedAt).toISOString().replace("T", " ").slice(0, 16)} UTC · ${durationS} s · ${agg.counters["vusers.created"] ?? 0} virtual users · ${agg.counters["http.requests"] ?? 0} requests (${(agg.rates?.["http.request_rate"] ?? 0).toFixed(1)} req/s mean)`);
lines.push("");

const rt = agg.summaries["http.response_time"] ?? {};
const all = codes(agg.counters);
const errors = Object.entries(agg.counters).filter(([k]) => k.startsWith("errors."));
lines.push("### Overall");
lines.push("");
lines.push("| Requests | 2xx | 4xx | 5xx | Errors (no response) | p50 | p95 | p99 | max |");
lines.push("|---|---|---|---|---|---|---|---|---|");
lines.push(`| ${agg.counters["http.requests"] ?? 0} | ${all["2xx"]} | ${all["4xx"]} | ${all["5xx"]} | ${count(agg.counters, "errors.")} | ${fmtMs(rt.p50 ?? rt.median)} | ${fmtMs(rt.p95)} | ${fmtMs(rt.p99)} | ${fmtMs(rt.max)} |`);
if (errors.length) {
  lines.push("");
  lines.push(errors.map(([k, v]) => `\`${k.slice(7)}\` × ${v}`).join(", "));
}
lines.push("");

lines.push("### Per request");
lines.push("");
lines.push("| Request | n | 2xx | 4xx | 5xx | p50 | p95 | p99 | max |");
lines.push("|---|---|---|---|---|---|---|---|---|");
const perEndpointCodes = {};
for (const [k, v] of Object.entries(agg.counters)) {
  const m = k.match(ENDPOINT_CODES);
  if (!m) continue;
  const [, name, code] = m;
  perEndpointCodes[name] ??= { "2xx": 0, "4xx": 0, "5xx": 0 };
  perEndpointCodes[name][`${code[0]}xx`] += v;
}
const endpointNames = Object.keys(agg.summaries)
  .filter((k) => k.startsWith(ENDPOINT_RT))
  .map((k) => k.slice(ENDPOINT_RT.length))
  .sort();
for (const name of endpointNames) {
  const s = agg.summaries[ENDPOINT_RT + name];
  const c = perEndpointCodes[name] ?? { "2xx": 0, "4xx": 0, "5xx": 0 };
  lines.push(`| \`${name}\` | ${s.count} | ${c["2xx"]} | ${c["4xx"]} | ${c["5xx"]} | ${fmtMs(s.p50 ?? s.median)} | ${fmtMs(s.p95)} | ${fmtMs(s.p99)} | ${fmtMs(s.max)} |`);
}
lines.push("");

lines.push("### Timeline (10 s windows)");
lines.push("");
lines.push("| t | arrivals/s | req/s | p50 | p95 | p99 | 5xx | no response |");
lines.push("|---|---|---|---|---|---|---|---|");
for (const w of windows) {
  const t = Math.round(((w.firstCounterAt ?? w.firstMetricAt) - startedAt) / 1000);
  const period = Math.max(1, Math.round(((w.lastCounterAt ?? w.lastMetricAt) - (w.firstCounterAt ?? w.firstMetricAt)) / 1000)) || 10;
  const wrt = w.summaries["http.response_time"] ?? {};
  const wc = codes(w.counters);
  const mm = String(Math.floor(t / 60)).padStart(2, "0");
  const ss = String(t % 60).padStart(2, "0");
  lines.push(`| ${mm}:${ss} | ${((w.counters["vusers.created"] ?? 0) / period).toFixed(1)} | ${((w.counters["http.requests"] ?? 0) / period).toFixed(1)} | ${fmtMs(wrt.p50 ?? wrt.median)} | ${fmtMs(wrt.p95)} | ${fmtMs(wrt.p99)} | ${wc["5xx"]} | ${count(w.counters, "errors.")} |`);
}
lines.push("");
console.log(lines.join("\n"));
