#!/usr/bin/env node
// Разбор лога gateway с прода: что реально шлёт приложение и из чего состоит время ответа.
//
//   ssh sloco 'cd /opt/backend_sloco && docker compose logs backend --no-log-prefix --since 2026-09-16T19:47:00Z' > session.log
//   node load/prod-session.mjs session.log            # хронология + разбивка по формам URL
//   node load/prod-session.mjs session.log --shapes   # только разбивка
//
// Читает JSON-строки pino: `request completed` (метод, url, статус, время) и
// `dependency metric` (каждый поход в Supabase / pg / рекомендер с его временем),
// склеивает их по reqId. lat/lng и q маскируются, id — схлопываются в :id.
import { readFileSync } from "node:fs";

const file = process.argv[2];
if (!file) {
  console.error("usage: node load/prod-session.mjs <gateway-log-file> [--shapes]");
  process.exit(1);
}
const shapesOnly = process.argv.includes("--shapes");

const reqs = new Map();
for (const line of readFileSync(file, "utf8").split("\n")) {
  if (!line) continue;
  let o;
  try { o = JSON.parse(line); } catch { continue; }
  if (!o.reqId) continue;
  const e = reqs.get(o.reqId) ?? { req: null, deps: [], resp: null };
  if (o.event === "request completed") e.req = o;
  else if (o.eventType === "metric" && o.metricType === "dependency") e.deps.push(o);
  else if (o.eventType === "response") e.resp = o;
  reqs.set(o.reqId, e);
}

const mask = (u) => u.replace(/(lat|lng)=[^&]*/g, "$1=…");
const shape = (u) =>
  mask(u)
    .replace(/\/v1\/places\/\d+/, "/v1/places/:id")
    .replace(/\/v1\/me\/places\/\d+/, "/v1/me/places/:id")
    .replace(/\/tiles\/\d+\/\d+\/\d+/, "/tiles/z/x/y")
    .replace(/collections\/[0-9a-f-]{20,}/, "collections/:id")
    .replace(/q=[^&]*/, "q=…")
    .replace(/category=[^&]*/, "category=…");
const pct = (arr, q) => {
  const s = [...arr].sort((a, b) => a - b);
  return s.length ? s[Math.min(s.length - 1, Math.floor(q * s.length))] : 0;
};
const hms = (t) => new Date(t).toISOString().slice(11, 19);
const depName = (d) => `${d.dependency}.${d.name}${d.success === false ? "(FAIL)" : ""}`;

const rows = [...reqs.values()]
  .filter((e) => e.req && !/^\/(metrics|v1\/health)/.test(e.req.url))
  .sort((a, b) => a.req.time - b.req.time);
if (rows.length === 0) {
  console.log("no requests in log");
  process.exit(0);
}
console.log(`requests: ${rows.length}, window ${hms(rows[0].req.time)}–${hms(rows.at(-1).req.time)} UTC\n`);

if (!shapesOnly) {
  console.log("## Хронология\n");
  for (const e of rows) {
    const r = e.req;
    const deps = e.deps.map((d) => `${depName(d)} ${d.durationMs}ms`).join("; ");
    const tail = e.resp?.cacheStatus ? ` {${e.resp.personalizationStatus ?? ""} ${e.resp.cacheStatus}}` : "";
    console.log(`${hms(r.time)}  ${r.method} ${mask(r.url)}  → ${r.statusCode} ${r.responseTimeMs}ms${tail}${deps ? "   [" + deps + "]" : ""}`);
  }
  console.log("");
}

console.log("## По формам URL\n");
const by = new Map();
for (const e of rows) {
  const k = `${e.req.method} ${shape(e.req.url)}`;
  const s = by.get(k) ?? { n: 0, total: [], depSum: [], nDeps: [], auth: [], codes: new Map(), deps: new Map() };
  s.n++;
  s.total.push(e.req.responseTimeMs);
  const ms = e.deps.map((d) => d.durationMs);
  s.depSum.push(ms.reduce((a, b) => a + b, 0));
  s.nDeps.push(ms.length);
  s.auth.push(e.deps.find((d) => d.name === "get_user")?.durationMs ?? 0);
  s.codes.set(e.req.statusCode, (s.codes.get(e.req.statusCode) ?? 0) + 1);
  for (const d of e.deps) {
    const arr = s.deps.get(depName(d)) ?? [];
    arr.push(d.durationMs);
    s.deps.set(depName(d), arr);
  }
  by.set(k, s);
}
const weight = (s) => pct(s.total, 0.5) * s.n;
for (const [k, s] of [...by].sort((a, b) => weight(b[1]) - weight(a[1]))) {
  console.log(`${s.n}×  ${k}`);
  console.log(
    `   total p50 ${pct(s.total, 0.5)}ms  p90 ${pct(s.total, 0.9)}ms  max ${Math.max(...s.total)}ms` +
      ` | походов/запрос ${pct(s.nDeps, 0.5)}  сумма походов p50 ${pct(s.depSum, 0.5)}ms  токен p50 ${pct(s.auth, 0.5)}ms` +
      ` | codes ${[...s.codes].map(([c, n]) => `${c}:${n}`).join(",")}`
  );
  for (const [name, arr] of [...s.deps].sort((a, b) => pct(b[1], 0.5) - pct(a[1], 0.5))) {
    console.log(`     ${name.padEnd(52)} ×${String(arr.length).padStart(3)}  p50 ${String(pct(arr, 0.5)).padStart(5)}ms  max ${String(Math.max(...arr)).padStart(5)}ms`);
  }
  console.log("");
}
