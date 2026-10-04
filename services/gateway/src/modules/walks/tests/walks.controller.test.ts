import { readFileSync } from "node:fs";
import type { InjectOptions } from "fastify";
import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { VersionedAppRoute } from "../../../config/routes.js";
import {
  WalkPlannerTimeoutError,
  WalkPlannerUnavailableError,
  type WalkPlannerClient,
  type WalkPlannerReply
} from "../../../lib/walk-planner-client.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import { WalksServiceImpl } from "../services/walks.service.js";
import type { WalksService } from "../index.js";

const user: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? user : null;
  }
};

const unused = async (): Promise<never> => {
  throw new Error("must not be called");
};

function fakeService(overrides: Partial<WalksService>): WalksService {
  return {
    config: unused,
    plan: unused,
    schedule: unused,
    insert: unused,
    searchPlaces: unused,
    place: unused,
    ...overrides
  };
}

async function inject(walksService: WalksService, request: InjectOptions) {
  const app = await buildApp({ authService, walksService });
  const response = await app.inject(request);
  await app.close();
  return response;
}

describe("walks routes", () => {
  it("plans for an anonymous user and passes the service status and body", async () => {
    const response = await inject(
      fakeService({
        async plan(input, context) {
          expect(input).toEqual({
            userId: null,
            body: { date: "2026-10-03" },
            query: { lang: "en" }
          });
          expect(context?.requestId).toBeTruthy();
          return { status: 200, body: { status: "no_candidates", variants: [] } };
        }
      }),
      {
        method: "POST",
        url: `${VersionedAppRoute.walksPlan}?lang=en`,
        payload: { date: "2026-10-03" }
      }
    );

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({ status: "no_candidates", variants: [] });
    expect(response.headers["x-request-id"]).toBeTruthy();
  });

  it("hands the signed-in user's id to the plan", async () => {
    const response = await inject(
      fakeService({
        async plan(input) {
          expect(input.userId).toBe(user.id);
          return { status: 200, body: {} };
        }
      }),
      {
        method: "POST",
        url: VersionedAppRoute.walksPlan,
        headers: { authorization: "Bearer valid-token" },
        payload: {}
      }
    );

    expect(response.statusCode).toBe(200);
  });

  it("refuses an invalid token with 401", async () => {
    const response = await inject(fakeService({}), {
      method: "POST",
      url: VersionedAppRoute.walksPlan,
      headers: { authorization: "Bearer nope" },
      payload: {}
    });

    expect(response.statusCode).toBe(401);
  });

  it("passes a service error envelope and Retry-After through", async () => {
    const body = { error: { code: "busy", message: "Занято", params: {} } };
    const response = await inject(
      fakeService({
        async schedule() {
          return { status: 503, body, retryAfter: "2" };
        }
      }),
      { method: "POST", url: VersionedAppRoute.walksSchedule, payload: { request: {}, sequence: [] } }
    );

    expect(response.statusCode).toBe(503);
    expect(response.headers["retry-after"]).toBe("2");
    expect(response.json()).toEqual(body);
  });

  it("passes a 422 with nested params verbatim", async () => {
    const body = {
      error: {
        code: "validation_error",
        message: "m",
        params: { errors: [{ loc: ["body", "slots"], type: "too_long" }] }
      }
    };
    const response = await inject(
      fakeService({
        async plan() {
          return { status: 422, body };
        }
      }),
      { method: "POST", url: VersionedAppRoute.walksPlan, payload: {} }
    );

    expect(response.statusCode).toBe(422);
    expect(response.json()).toEqual(body);
  });

  it("answers 503 walk_planner_unavailable in the request language", async () => {
    const response = await inject(
      fakeService({
        async config() {
          throw new WalkPlannerUnavailableError("connection refused");
        }
      }),
      { method: "GET", url: `${VersionedAppRoute.walksConfig}?lang=en` }
    );

    expect(response.statusCode).toBe(503);
    expect(response.headers["retry-after"]).toBe("5");
    expect(response.json()).toEqual({
      error: {
        code: "walk_planner_unavailable",
        message: "The walk planner is temporarily unavailable. Try again.",
        params: {}
      }
    });
  });

  it("answers 504 walk_planner_timeout", async () => {
    const response = await inject(
      fakeService({
        async plan() {
          throw new WalkPlannerTimeoutError("plan", 20_000);
        }
      }),
      { method: "POST", url: VersionedAppRoute.walksPlan, payload: {} }
    );

    expect(response.statusCode).toBe(504);
    expect(response.json().error.code).toBe("walk_planner_timeout");
  });

  it("refuses a non-object body and bad query types with 400", async () => {
    const service = fakeService({});

    const array = await inject(service, {
      method: "POST",
      url: VersionedAppRoute.walksPlan,
      payload: [1, 2]
    });
    const lang = await inject(service, {
      method: "GET",
      url: `${VersionedAppRoute.walksConfig}?lang=de`
    });
    const sourceId = await inject(service, {
      method: "GET",
      url: VersionedAppRoute.walksPlace.replace(":sourceId", "12a")
    });

    expect([array.statusCode, lang.statusCode, sourceId.statusCode]).toEqual([400, 400, 400]);
  });

  it("parses search and place queries", async () => {
    const response = await inject(
      fakeService({
        async searchPlaces(query) {
          expect(query).toEqual({ q: "cafe", lat: 44.4, lon: 26.1, limit: 5, includeClosed: true });
          return { status: 200, body: { results: [] } };
        },
        async place(input) {
          expect(input).toEqual({ sourceId: "10915586233752676659", query: { city: "Bucharest" } });
          return { status: 200, body: { sourceId: input.sourceId } };
        }
      }),
      {
        method: "GET",
        url: `${VersionedAppRoute.walksPlacesSearch}?q=cafe&lat=44.4&lon=26.1&limit=5&includeClosed=true`
      }
    );
    expect(response.statusCode).toBe(200);

    const place = await inject(
      fakeService({
        async place(input) {
          return { status: 200, body: { sourceId: input.sourceId } };
        }
      }),
      {
        method: "GET",
        url: `${VersionedAppRoute.walksPlace.replace(":sourceId", "10915586233752676659")}?city=Bucharest`
      }
    );
    expect(place.json()).toEqual({ sourceId: "10915586233752676659" });
  });

  it("serializes a full recorded plan and insert without dropping a field", async () => {
    const golden = JSON.parse(
      readFileSync(
        new URL(
          "../../../../../walk-planner/golden/expected/bucharest-20261002-d68a311e/S01_default.json",
          import.meta.url
        ),
        "utf8"
      )
    ) as {
      plan: { response: unknown };
      edits: Record<string, Array<{ call: { call: string; response: unknown } }>>;
    };
    const insertCall = Object.values(golden.edits)
      .flat()
      .find((edit) => edit.call.call === "POST /v1/walks/insert");

    const reply = (body: unknown): WalkPlannerReply => ({ status: 200, body });
    const client: WalkPlannerClient = {
      config: unused,
      plan: async () => reply(golden.plan.response),
      schedule: unused,
      insert: async () => reply(insertCall?.call.response),
      searchPlaces: unused,
      place: unused
    };
    const service = new WalksServiceImpl(
      client,
      { placeIdsBySourceIds: async (ids) => new Map(ids.map((id, i) => [id, i + 1])) },
      { getUserSignals: unused }
    );

    for (const [url, call] of [
      [VersionedAppRoute.walksPlan, () => service.plan({ userId: null, body: {}, query: {} })],
      [VersionedAppRoute.walksInsert, () => service.insert({ body: {}, query: {} })]
    ] as const) {
      const expected = (await call()).body;
      const response = await inject(service, { method: "POST", url, payload: {} });

      expect(response.statusCode).toBe(200);
      expect(response.json()).toEqual(expected);
    }

    expect(insertCall).toBeDefined();
  });
});
