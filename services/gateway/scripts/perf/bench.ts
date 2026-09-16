import "dotenv/config";
import { writeFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import pg from "pg";

// Perf bench for the hot paths (SLO-3). One command, one table: for every case
// the FIRST run (cold — the only run that reads from disk) and the p50 / p95 / max
// of the remaining WARM runs, plus the buffers Postgres touched. Paste the table
// into the Linear task you are closing; a perf fix is not done without it.
//
//   pnpm perf:bench                       # SQL layer, 5 runs per case
//   pnpm perf:bench --runs 10             # more samples
//   pnpm perf:bench --http https://api…   # also hit the gateway over HTTP
//   pnpm perf:bench --only search,tile    # substring filter on case names
//   pnpm perf:bench --out bench.md        # write the markdown too
//
// SQL timings are SERVER-SIDE (`EXPLAIN (ANALYZE, BUFFERS, TIMING OFF)`), so the
// pooler round-trip does not blur them; `read` blocks > 0 means the run hit disk.
// HTTP timings are wall-clock from this machine. "Cold" is soft: the pooler on
// :6543 shares one Postgres cache, so the first run is only as cold as the
// previous minutes of traffic left it.

type SqlCase = {
  name: string;
  sql: string;
  params: unknown[];
};

type HttpCase = {
  name: string;
  path: string;
};

type SqlSample = {
  execMs: number;
  sharedHit: number;
  sharedRead: number;
  tempRead: number;
  tempWritten: number;
};

type CaseReport = {
  name: string;
  first: SqlSample | { execMs: number };
  warm: number[];
  note?: string;
};

// Reference points. Bucharest / Tbilisi centres, and the `cafe` bucket keywords
// exactly as services/gateway/src/modules/places/common/place-buckets.ts (dev)
// sends them to the RPCs. Keep in sync by hand — the script must stay standalone.
const BUCHAREST = { lat: 44.4268, lng: 26.1025, city: "Bucharest", country: "Romania" };
const TBILISI = { lat: 41.7151, lng: 44.8271, city: "Tbilisi", country: "Georgia" };
const CAFE_KEYWORDS = [
  "cafe",
  "coffee",
  "bakery",
  "patisserie",
  "pastry",
  "dessert",
  "confectionery",
  "sweets",
  "tea house"
];

function parseArgs(argv: string[]) {
  const args = { runs: 5, http: "", only: [] as string[], out: "" };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      return argv[i] ?? "";
    };
    if (arg === "--runs") args.runs = Math.max(2, Number(next()) || 5);
    else if (arg === "--http") args.http = next().replace(/\/$/, "");
    else if (arg === "--only") args.only = next().split(",").map((s) => s.trim()).filter(Boolean);
    else if (arg === "--out") args.out = next();
  }
  return args;
}

// Slippy-map tile for a lat/lng at zoom z (same math as the client and map_tile()).
function tileFor(lat: number, lng: number, z: number) {
  const n = 2 ** z;
  const x = Math.floor(((lng + 180) / 360) * n);
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n);
  return { z, x, y };
}

function percentile(values: number[], p: number) {
  if (values.length === 0) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1);
  return sorted[Math.max(0, idx)];
}

function ms(value: number) {
  if (!Number.isFinite(value)) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${Math.round(value)} ms`;
}

function blocks(value: number) {
  return value.toLocaleString("en-US");
}

async function sqlCases(client: pg.Client): Promise<SqlCase[]> {
  // 200 ids the recommender would plausibly return for Bucharest: the hydration
  // RPC is only slow at this width.
  const ids = await client.query<{ source_id: string }>(
    `select source_id from public.places
      where city = $1
      order by map_visibility_score desc nulls last, id
      limit 200`,
    [BUCHAREST.city]
  );
  const sourceIds = ids.rows.map((r) => r.source_id);
  const bucTile = tileFor(BUCHAREST.lat, BUCHAREST.lng, 13);
  const tbiTile = tileFor(TBILISI.lat, TBILISI.lng, 13);

  const search = (q: string | null, keywords: string[] | null) => ({
    sql: `select * from public.search_places(
            q => $1, user_lat => $2, user_lng => $3, user_city => $4, user_country => $5,
            result_limit => $6, category_keywords => $7, radius_meters => $8)`,
    params: [q, BUCHAREST.lat, BUCHAREST.lng, BUCHAREST.city, BUCHAREST.country, 20, keywords, null]
  });

  return [
    { name: "search_places cafe @Bucharest", ...search("cafe", null) },
    { name: "search_places pizza @Bucharest", ...search("pizza", null) },
    { name: "search_places browse cafe-bucket @Bucharest", ...search(null, CAFE_KEYWORDS) },
    {
      name: "feed_fallback_places Bucharest limit 200",
      sql: `select * from public.feed_fallback_places(
              user_lat => $1, user_lng => $2, user_city => $3, user_country => $4,
              result_limit => $5, category_keywords => $6)`,
      params: [BUCHAREST.lat, BUCHAREST.lng, BUCHAREST.city, BUCHAREST.country, 200, null]
    },
    {
      name: "feed_fallback_places Berlin cafe-bucket limit 200",
      sql: `select * from public.feed_fallback_places(
              user_lat => $1, user_lng => $2, user_city => $3, user_country => $4,
              result_limit => $5, category_keywords => $6)`,
      params: [null, null, "Berlin", null, 200, CAFE_KEYWORDS]
    },
    {
      name: "feed_places_by_source_ids 200 ids @Bucharest",
      sql: `select * from public.feed_places_by_source_ids(
              source_ids => $1, user_lat => $2, user_lng => $3, result_limit => $4)`,
      params: [sourceIds, BUCHAREST.lat, BUCHAREST.lng, 200]
    },
    {
      name: `map_tile z13 Bucharest ${bucTile.x}/${bucTile.y}`,
      sql: `select public.map_tile($1, $2, $3)`,
      params: [bucTile.z, bucTile.x, bucTile.y]
    },
    {
      name: `map_tile z13 Tbilisi ${tbiTile.x}/${tbiTile.y}`,
      sql: `select public.map_tile($1, $2, $3)`,
      params: [tbiTile.z, tbiTile.x, tbiTile.y]
    },
    {
      name: "cities aggregate (GET /v1/cities)",
      sql: `select p.city as name, p.country, count(*)::int as place_count
              from public.places p
             where p.city is not null and p.country is not null
             group by p.city, p.country
             order by place_count desc`,
      params: []
    }
  ];
}

async function explainOnce(client: pg.Client, c: SqlCase): Promise<SqlSample> {
  const res = await client.query(
    `explain (analyze, buffers, timing off, format json) ${c.sql}`,
    c.params
  );
  const plan = (res.rows[0]["QUERY PLAN"] as Array<Record<string, unknown>>)[0];
  const root = plan.Plan as Record<string, number>;
  return {
    execMs: Number(plan["Execution Time"]),
    sharedHit: Number(root["Shared Hit Blocks"] ?? 0),
    sharedRead: Number(root["Shared Read Blocks"] ?? 0),
    tempRead: Number(root["Temp Read Blocks"] ?? 0),
    tempWritten: Number(root["Temp Written Blocks"] ?? 0)
  };
}

async function runSql(client: pg.Client, cases: SqlCase[], runs: number): Promise<CaseReport[]> {
  const reports: CaseReport[] = [];
  for (const c of cases) {
    process.stderr.write(`  ${c.name} …`);
    try {
      const first = await explainOnce(client, c);
      const warm: number[] = [];
      for (let i = 1; i < runs; i += 1) {
        warm.push((await explainOnce(client, c)).execMs);
      }
      reports.push({ name: c.name, first, warm });
      process.stderr.write(` ${ms(first.execMs)} → warm p50 ${ms(percentile(warm, 50))}\n`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      reports.push({ name: c.name, first: { execMs: NaN }, warm: [], note: message });
      process.stderr.write(` ERROR ${message}\n`);
    }
  }
  return reports;
}

async function httpCases(client: pg.Client): Promise<HttpCase[]> {
  const place = await client.query<{ id: number }>(
    `select id from public.places where city = $1 order by map_visibility_score desc nulls last, id limit 1`,
    [BUCHAREST.city]
  );
  const placeId = place.rows[0]?.id;
  const t = tileFor(BUCHAREST.lat, BUCHAREST.lng, 13);
  const q = (o: Record<string, string | number>) =>
    Object.entries(o)
      .map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`)
      .join("&");
  return [
    { name: "GET /v1/feed/places?limit=20", path: `/v1/feed/places?limit=20` },
    {
      name: "GET /v1/feed/places?limit=20&city=Bucharest",
      path: `/v1/feed/places?${q({ limit: 20, city: BUCHAREST.city })}`
    },
    {
      name: "GET /v1/feed/places?limit=20&category=cafe&city=Berlin",
      path: `/v1/feed/places?${q({ limit: 20, category: "cafe", city: "Berlin" })}`
    },
    {
      name: "GET /v1/search/places?q=cafe @Bucharest",
      path: `/v1/search/places?${q({ q: "cafe", lat: BUCHAREST.lat, lng: BUCHAREST.lng, city: BUCHAREST.city, country: BUCHAREST.country })}`
    },
    { name: "GET /v1/cities", path: `/v1/cities` },
    { name: `GET /v1/map/tiles/${t.z}/${t.x}/${t.y}.mvt`, path: `/v1/map/tiles/${t.z}/${t.x}/${t.y}.mvt` },
    ...(placeId ? [{ name: `GET /v1/places/${placeId}`, path: `/v1/places/${placeId}` }] : [])
  ];
}

async function runHttp(base: string, cases: HttpCase[], runs: number): Promise<CaseReport[]> {
  const reports: CaseReport[] = [];
  for (const c of cases) {
    process.stderr.write(`  ${c.name} …`);
    const timings: number[] = [];
    let status = 0;
    for (let i = 0; i < runs; i += 1) {
      const started = performance.now();
      try {
        const res = await fetch(base + c.path, { headers: { accept: "*/*" } });
        await res.arrayBuffer();
        status = res.status;
      } catch (error) {
        status = -1;
        process.stderr.write(` ERROR ${error instanceof Error ? error.message : String(error)}`);
        break;
      }
      timings.push(performance.now() - started);
    }
    const [first = NaN, ...warm] = timings;
    reports.push({ name: c.name, first: { execMs: first }, warm, note: status === 200 ? undefined : `HTTP ${status}` });
    process.stderr.write(` HTTP ${status} ${ms(first)} → warm p50 ${ms(percentile(warm, 50))}\n`);
  }
  return reports;
}

function renderSql(reports: CaseReport[], runs: number) {
  const lines = [
    `| Case | Cold (1st) | shared read / hit | temp r/w | Warm p50 | Warm p95 | Warm max |`,
    `|---|---|---|---|---|---|---|`
  ];
  for (const r of reports) {
    const f = r.first as SqlSample;
    const buf = "sharedHit" in f ? `${blocks(f.sharedRead)} / ${blocks(f.sharedHit)}` : "—";
    const temp = "tempRead" in f ? `${blocks(f.tempRead)} / ${blocks(f.tempWritten)}` : "—";
    lines.push(
      `| ${r.name}${r.note ? ` ⚠ ${r.note}` : ""} | ${ms(f.execMs)} | ${buf} | ${temp} | ${ms(percentile(r.warm, 50))} | ${ms(percentile(r.warm, 95))} | ${ms(Math.max(...r.warm))} |`
    );
  }
  lines.push("", `_SQL: server-side execution time via EXPLAIN ANALYZE; ${runs} runs per case, first = cold, rest = warm. Blocks are 8 KB._`);
  return lines.join("\n");
}

function renderHttp(reports: CaseReport[], runs: number, base: string) {
  const lines = [
    `| Endpoint | Cold (1st) | Warm p50 | Warm p95 | Warm max |`,
    `|---|---|---|---|---|`
  ];
  for (const r of reports) {
    lines.push(
      `| ${r.name}${r.note ? ` ⚠ ${r.note}` : ""} | ${ms(r.first.execMs)} | ${ms(percentile(r.warm, 50))} | ${ms(percentile(r.warm, 95))} | ${ms(Math.max(...r.warm))} |`
    );
  }
  lines.push("", `_HTTP: wall-clock from this machine against ${base}; ${runs} runs per endpoint._`);
  return lines.join("\n");
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const connectionString = process.env.SUPABASE_DB_URL;
  if (!connectionString) {
    throw new Error("SUPABASE_DB_URL is required (services/gateway/.env, copied from /opt/backend_sloco/.env on the server)");
  }

  const client = new pg.Client({ connectionString, statement_timeout: 120_000 });
  await client.connect();
  const meta = await client.query<{ host: string; now: string }>(
    `select current_setting('server_version') as host, now()::text as now`
  );

  const stamp = new Date().toISOString().slice(0, 16).replace("T", " ") + " UTC";
  const header = [
    `## Perf bench — ${stamp}`,
    ``,
    `PostgreSQL ${meta.rows[0].host}, ${args.runs} runs/case${args.http ? `, HTTP against ${args.http}` : ""}.`,
    ``
  ];

  const filter = (name: string) => args.only.length === 0 || args.only.some((s) => name.toLowerCase().includes(s.toLowerCase()));

  process.stderr.write("SQL layer\n");
  const sql = (await sqlCases(client)).filter((c) => filter(c.name));
  const sqlReports = await runSql(client, sql, args.runs);
  const sections = [...header, `### SQL (RPC bodies as the gateway calls them)`, ``, renderSql(sqlReports, args.runs)];

  if (args.http) {
    process.stderr.write("HTTP layer\n");
    const http = (await httpCases(client)).filter((c) => filter(c.name));
    const httpReports = await runHttp(args.http, http, args.runs);
    sections.push(``, `### HTTP (gateway)`, ``, renderHttp(httpReports, args.runs, args.http));
  }

  await client.end();

  const markdown = sections.join("\n") + "\n";
  process.stdout.write(markdown);
  if (args.out) {
    await writeFile(args.out, markdown, "utf8");
    process.stderr.write(`written ${args.out}\n`);
  }
}

main().catch((error) => {
  process.stderr.write(`bench failed: ${error instanceof Error ? error.stack ?? error.message : String(error)}\n`);
  process.exitCode = 1;
});
