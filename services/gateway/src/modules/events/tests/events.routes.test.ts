import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { VersionedAppRoute } from "../../../config/routes.js";
import type {
  AuthService,
  AuthenticatedUser
} from "../../auth/auth.service.js";
import { createEventsService } from "../services/events.service.js";
import type { EventRow, EventsStoreContract } from "../common/events.types.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

class FakeEventsStore implements EventsStoreContract {
  rows: EventRow[] = [];
  links: Array<{ userId: string; anonIds: string[] }> = [];
  private readonly seenEventIds = new Set<string>();

  async insertEvents(rows: EventRow[]): Promise<number> {
    let inserted = 0;
    for (const row of rows) {
      if (this.seenEventIds.has(row.event_id)) continue;
      this.seenEventIds.add(row.event_id);
      this.rows.push(row);
      inserted += 1;
    }
    return inserted;
  }

  async linkIdentities(userId: string, anonIds: string[]): Promise<void> {
    if (anonIds.length > 0) {
      this.links.push({ userId, anonIds });
    }
  }
}

function buildEventsApp(store = new FakeEventsStore()) {
  return buildApp({
    authService,
    eventsService: createEventsService(store)
  }).then((app) => ({ app, store }));
}

const BATCH_ID = "7d9a2f6e-6a1f-4a5e-9d3e-2b1c8f4e5a6b";

function makeEvent(overrides: Record<string, unknown> = {}) {
  return {
    event_id: crypto.randomUUID(),
    event_type: "card_open",
    client_ts: "2026-08-16T14:03:21+03:00",
    seq: 1,
    anon_id: "anon-device-1",
    session_id: "session-1",
    surface: "feed",
    context: {
      request_id: "e0e2bafc-6e15-4d55-a1b0-97f3a8d1c111",
      position: 5,
      mode: "match_vibe"
    },
    place_id: "1234567890",
    payload: { source: "feed_card" },
    ...overrides
  };
}

describe("POST /v1/events", () => {
  it("accepts an anonymous batch and stores events with null user_id", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload: { batch_id: BATCH_ID, device: { os: "iOS 27" }, events: [makeEvent()] }
    });

    await app.close();

    expect(response.statusCode).toBe(202);
    expect(response.json()).toEqual({
      accepted: 1,
      duplicates: 0,
      rejected: []
    });
    expect(store.rows).toHaveLength(1);
    expect(store.rows[0]).toMatchObject({
      event_type: "card_open",
      known_type: true,
      user_id: null,
      anon_id: "anon-device-1",
      request_id: "e0e2bafc-6e15-4d55-a1b0-97f3a8d1c111",
      position: 5,
      device: { os: "iOS 27" }
    });
    expect(store.links).toHaveLength(0);
  });

  it("takes user_id from the token and ignores the body's user_id", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      headers: { authorization: "Bearer valid-token" },
      payload: {
        batch_id: BATCH_ID,
        events: [makeEvent({ user_id: "someone-else" })]
      }
    });

    await app.close();

    expect(response.statusCode).toBe(202);
    expect(store.rows[0]?.user_id).toBe(authenticatedUser.id);
  });

  it("links anon_id to the account on an authenticated batch", async () => {
    const { app, store } = await buildEventsApp();

    await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      headers: { authorization: "Bearer valid-token" },
      payload: {
        batch_id: BATCH_ID,
        events: [
          makeEvent({ anon_id: "anon-device-1" }),
          makeEvent({ anon_id: "anon-device-1" })
        ]
      }
    });

    await app.close();

    expect(store.links).toEqual([
      { userId: authenticatedUser.id, anonIds: ["anon-device-1"] }
    ]);
  });

  it("rejects an invalid token instead of writing anonymous history", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      headers: { authorization: "Bearer wrong-token" },
      payload: { batch_id: BATCH_ID, events: [makeEvent()] }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(store.rows).toHaveLength(0);
  });

  it("counts a resent batch as duplicates, not new rows", async () => {
    const { app, store } = await buildEventsApp();
    const payload = {
      batch_id: BATCH_ID,
      events: [makeEvent(), makeEvent()]
    };

    const first = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload
    });
    const second = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload
    });

    await app.close();

    expect(first.json()).toMatchObject({ accepted: 2, duplicates: 0 });
    expect(second.json()).toMatchObject({ accepted: 0, duplicates: 2 });
    expect(store.rows).toHaveLength(2);
  });

  it("keeps unknown event types with known_type=false", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload: {
        batch_id: BATCH_ID,
        events: [makeEvent({ event_type: "brand_new_type" })]
      }
    });

    await app.close();

    expect(response.statusCode).toBe(202);
    expect(store.rows[0]).toMatchObject({
      event_type: "brand_new_type",
      known_type: false
    });
  });

  it("rejects only the invalid events and keeps their neighbours", async () => {
    const { app, store } = await buildEventsApp();
    const good = makeEvent();
    const badTs = makeEvent({ client_ts: "not-a-date" });
    const badId = makeEvent({ event_id: "not-a-uuid" });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload: { batch_id: BATCH_ID, events: [badTs, good, badId, 42] }
    });

    await app.close();

    expect(response.statusCode).toBe(202);
    expect(response.json()).toEqual({
      accepted: 1,
      duplicates: 0,
      rejected: [
        { event_id: badTs.event_id, reason: "bad_client_ts" },
        { event_id: "not-a-uuid", reason: "bad_event_id" },
        { event_id: null, reason: "not_an_object" }
      ]
    });
    expect(store.rows).toHaveLength(1);
    expect(store.rows[0]?.event_id).toBe(good.event_id);
  });

  it("returns 429 for a batch over 500 events", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload: {
        batch_id: BATCH_ID,
        events: Array.from({ length: 501 }, () => makeEvent())
      }
    });

    await app.close();

    expect(response.statusCode).toBe(429);
    expect(store.rows).toHaveLength(0);
  });

  it("returns 429 for a body over 1 MiB", async () => {
    const { app, store } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      headers: { "content-type": "application/json" },
      payload: JSON.stringify({
        batch_id: BATCH_ID,
        events: [makeEvent({ payload: { blob: "x".repeat(1_100_000) } })]
      })
    });

    await app.close();

    expect(response.statusCode).toBe(429);
    expect(store.rows).toHaveLength(0);
  });

  it("returns 400 for a broken envelope", async () => {
    const { app } = await buildEventsApp();

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.events,
      payload: { batch_id: "not-a-uuid", events: [] }
    });

    await app.close();

    expect(response.statusCode).toBe(400);
    expect(response.json()).toMatchObject({
      status: "error",
      message: "Invalid events batch"
    });
  });
});
