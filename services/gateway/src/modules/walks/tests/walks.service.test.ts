import { describe, expect, it } from "vitest";
import type {
  WalkPlannerClient,
  WalkPlannerReply
} from "../../../lib/walk-planner-client.js";
import { WalksServiceImpl } from "../services/walks.service.js";
import type {
  WalksSignalsSource,
  WalksStoreContract
} from "../common/walks.types.js";

type Call = { operation: string; body?: unknown; query: unknown; path?: string };

function fakeClient(reply: WalkPlannerReply) {
  const calls: Call[] = [];
  const answer = async () => reply;
  const client: WalkPlannerClient = {
    config: async (query) => (calls.push({ operation: "config", query }), answer()),
    plan: async (body, query) => (calls.push({ operation: "plan", body, query }), answer()),
    schedule: async (body, query) => (calls.push({ operation: "schedule", body, query }), answer()),
    insert: async (body, query) => (calls.push({ operation: "insert", body, query }), answer()),
    searchPlaces: async (query) => (calls.push({ operation: "search", query }), answer()),
    place: async (sourceId, query) =>
      (calls.push({ operation: "place", path: sourceId, query }), answer())
  };
  return { client, calls };
}

function fakeStore(known: Record<string, number>) {
  const lookups: string[][] = [];
  const store: WalksStoreContract = {
    async placeIdsBySourceIds(sourceIds) {
      lookups.push(sourceIds);
      return new Map(
        sourceIds.flatMap((id) => (id in known ? [[id, known[id]!] as const] : []))
      );
    }
  };
  return { store, lookups };
}

const noSignals: WalksSignalsSource = {
  async getUserSignals() {
    throw new Error("must not be called for an anonymous user");
  }
};

const planReply: WalkPlannerReply = {
  status: 200,
  body: {
    plan_id: "p",
    status: "ok",
    request: {
      start: { lat: 1, lon: 2, place_id: "111" },
      favourite_place_ids: ["111"]
    },
    variants: [
      {
        stops: [{ place_id: "111", kind: "slot" }, { place_id: "222", kind: "on_the_way" }],
        sequence: [{ place_id: "111", kind: "slot", dwell_min: 60 }]
      }
    ],
    places: {
      "111": { place_id: "111", name: "A" },
      "222": { place_id: "222", name: "B" }
    }
  }
};

describe("walks service", () => {
  it("plans for an anonymous user without favourites and strips server-owned keys", async () => {
    const { client, calls } = fakeClient(planReply);
    const service = new WalksServiceImpl(client, fakeStore({}).store, noSignals);

    await service.plan({
      userId: null,
      body: {
        date: "2026-10-03",
        startTime: "10:00",
        mustVisitSourceIds: ["9"],
        favouriteSourceIds: ["evil"],
        wantToGoSourceIds: ["evil"],
        topK: 50,
        debug: true
      },
      query: { lang: "en", geometry: "polyline6" }
    });

    expect(calls[0]).toEqual({
      operation: "plan",
      body: { date: "2026-10-03", start_time: "10:00", must_visit_place_ids: ["9"] },
      query: { lang: "en", geometry: "polyline6" }
    });
  });

  it("fills favourites from the user's saved places: favourites first, overlap counted once, 500 cap", async () => {
    const { client, calls } = fakeClient(planReply);
    const favourites = Array.from({ length: 498 }, (_v, i) => `f${i}`);
    const signals: WalksSignalsSource = {
      async getUserSignals(userId) {
        expect(userId).toBe("user-1");
        return {
          favouritesPlaceIds: [...favourites, "f0"],
          wantToGoPlaceIds: ["f1", "w1", "w2", "w3"]
        };
      }
    };
    const service = new WalksServiceImpl(client, fakeStore({}).store, signals);

    await service.plan({ userId: "user-1", body: { date: "2026-10-03" }, query: {} });

    const body = calls[0]?.body as Record<string, string[]>;
    expect(body.favourite_place_ids).toEqual(favourites);
    expect(body.want_to_go_place_ids).toEqual(["w1", "w2"]);
  });

  it("sends no favourite keys for a user without saved places", async () => {
    const { client, calls } = fakeClient(planReply);
    const signals: WalksSignalsSource = {
      async getUserSignals() {
        return { favouritesPlaceIds: [], wantToGoPlaceIds: [] };
      }
    };
    const service = new WalksServiceImpl(client, fakeStore({}).store, signals);

    await service.plan({ userId: "user-1", body: { date: "2026-10-03" }, query: {} });

    expect(calls[0]?.body).toEqual({ date: "2026-10-03" });
  });

  it("adds placeId to stops and cards in one lookup, never to the echo or sequence", async () => {
    const { client } = fakeClient(planReply);
    const { store, lookups } = fakeStore({ "111": 812 });
    const service = new WalksServiceImpl(client, store, noSignals);

    const reply = await service.plan({ userId: null, body: {}, query: {} });

    expect(lookups).toEqual([["111", "222"]]);
    expect(reply.body).toEqual({
      planId: "p",
      status: "ok",
      request: {
        start: { lat: 1, lon: 2, sourceId: "111" },
        favouriteSourceIds: ["111"]
      },
      variants: [
        {
          stops: [
            { sourceId: "111", placeId: 812, kind: "slot" },
            { sourceId: "222", placeId: null, kind: "on_the_way" }
          ],
          sequence: [{ sourceId: "111", kind: "slot", dwellMin: 60 }]
        }
      ],
      places: {
        "111": { sourceId: "111", placeId: 812, name: "A" },
        "222": { sourceId: "222", placeId: null, name: "B" }
      }
    });
  });

  it("passes edits through with the echo intact and enriches variant.stops", async () => {
    const { client, calls } = fakeClient({
      status: 200,
      body: {
        variant: { stops: [{ place_id: "111" }] },
        places: { "111": { place_id: "111" } },
        inserted_index: 0
      }
    });
    const service = new WalksServiceImpl(client, fakeStore({ "111": 7 }).store, noSignals);

    const reply = await service.insert({
      body: {
        request: { favouriteSourceIds: ["111"], startTime: "10:00" },
        sequence: [{ sourceId: "222", kind: "slot", placeId: 5 }],
        sourceId: "111",
        allowTemporarilyClosed: true
      },
      query: {}
    });

    expect(calls[0]?.body).toEqual({
      request: { favourite_place_ids: ["111"], start_time: "10:00" },
      sequence: [{ place_id: "222", kind: "slot" }],
      place_id: "111",
      allow_temporarily_closed: true
    });
    expect(reply.body).toEqual({
      variant: { stops: [{ sourceId: "111", placeId: 7 }] },
      places: { "111": { sourceId: "111", placeId: 7 } },
      insertedIndex: 0
    });
  });

  it("enriches search results and the place screen; converts includeClosed", async () => {
    const search = fakeClient({
      status: 200,
      body: { results: [{ place_id: "111", distance_m: 5 }] }
    });
    const service = new WalksServiceImpl(search.client, fakeStore({ "111": 3 }).store, noSignals);

    const reply = await service.searchPlaces({ q: "cafe", includeClosed: true });

    expect(search.calls[0]?.query).toEqual({ q: "cafe", include_closed: true });
    expect(reply.body).toEqual({ results: [{ sourceId: "111", placeId: 3, distanceM: 5 }] });

    const place = fakeClient({ status: 200, body: { place_id: "111", title_ru: "x" } });
    const placeService = new WalksServiceImpl(place.client, fakeStore({}).store, noSignals);

    expect((await placeService.place({ sourceId: "111", query: {} })).body).toEqual({
      sourceId: "111",
      placeId: null,
      titleRu: "x"
    });
  });

  it("caches config and the place screen across users, only 200 answers", async () => {
    const memory = new Map<string, unknown>();
    const cacheStore = {
      kind: "redis" as const,
      async get<T>(key: string) {
        return (memory.get(key) as T | undefined) ?? null;
      },
      async set<T>(key: string, value: T) {
        memory.set(key, value);
      },
      getBuffer: async () => null,
      setBuffer: async () => undefined,
      del: async () => undefined,
      delByPrefix: async () => undefined
    };
    let status = 404;
    const calls: string[] = [];
    const client = {
      ...fakeClient(planReply).client,
      place: async (sourceId: string) => {
        calls.push(sourceId);
        return status === 200
          ? { status, body: { place_id: sourceId } }
          : { status, body: { error: { code: "unknown_place", message: "", params: {} } } };
      }
    };
    const service = new WalksServiceImpl(client, fakeStore({ "111": 9 }).store, noSignals, cacheStore);
    const ask = () => service.place({ sourceId: "111", query: { lang: "en" } });

    expect((await ask()).status).toBe(404);
    expect(memory.size).toBe(0);

    status = 200;
    expect((await ask()).body).toEqual({ sourceId: "111", placeId: 9 });
    expect(await ask()).toEqual({ status: 200, body: { sourceId: "111", placeId: 9 } });
    expect(calls).toEqual(["111", "111"]);
    expect([...memory.keys()]).toEqual(["walks:v1:place:111:_:en"]);
  });

  it("passes service errors through without a database lookup", async () => {
    const { client } = fakeClient({
      status: 503,
      body: { error: { code: "busy", message: "m", params: { retry_after_s: 2 } } },
      retryAfter: "2"
    });
    const { store, lookups } = fakeStore({});
    const service = new WalksServiceImpl(client, store, noSignals);

    const reply = await service.plan({ userId: null, body: {}, query: {} });

    expect(reply).toEqual({
      status: 503,
      body: { error: { code: "busy", message: "m", params: { retry_after_s: 2 } } },
      retryAfter: "2"
    });
    expect(lookups).toEqual([]);
  });
});
