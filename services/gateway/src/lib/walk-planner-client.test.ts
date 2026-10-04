import { describe, expect, it, vi } from "vitest";
import {
  createWalkPlannerClient,
  WalkPlannerTimeoutError,
  WalkPlannerUnavailableError
} from "./walk-planner-client.js";

const BASE_URL = "http://walk-planner:8000";

type Step =
  | { status: number; body: unknown; retryAfter?: string }
  | { error: "ECONNREFUSED" | "ECONNRESET" | "ENOTFOUND" }
  | { hang: true };

// A fake transport: fetch answers the scripted steps in order; sleep and the
// clock are virtual, so retry pauses cost no wall time.
function harness(steps: Step[]) {
  let clock = 0;
  const sleeps: number[] = [];
  const requests: Array<{ url: string; init: RequestInit }> = [];

  const fetchFake = (async (url: URL, init: RequestInit) => {
    requests.push({ url: url.toString(), init });
    const step = steps.shift();

    if (!step) throw new Error("unexpected extra request");

    if ("hang" in step) {
      return new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () =>
          reject(new DOMException("aborted", "AbortError"))
        );
      });
    }

    if ("error" in step) {
      throw Object.assign(new TypeError("fetch failed"), {
        cause: { code: step.error }
      });
    }

    return new Response(JSON.stringify(step.body), {
      status: step.status,
      headers: step.retryAfter ? { "retry-after": step.retryAfter } : {}
    });
  }) as unknown as typeof fetch;

  const client = createWalkPlannerClient({
    baseUrl: BASE_URL,
    fetch: fetchFake,
    now: () => clock,
    random: () => 0,
    sleep: async (ms) => {
      sleeps.push(ms);
      clock += ms;
    }
  });

  return { client, sleeps, requests, pending: () => steps.length };
}

const busy = {
  status: 503,
  body: { error: { code: "busy", message: "", params: {} } },
  retryAfter: "2"
};
const ok = { status: 200, body: { status: "ok" } };

describe("walk-planner client", () => {
  it("sends the body, query and request id and returns status + body", async () => {
    const { client, requests } = harness([ok]);

    const reply = await client.plan(
      { city: "Bucharest" },
      { lang: "en", geometry: undefined },
      { requestId: "req-7" }
    );

    expect(reply).toEqual({ status: 200, body: { status: "ok" }, retryAfter: undefined });
    expect(requests[0]?.url).toBe(`${BASE_URL}/v1/walks/plan?lang=en`);
    expect(requests[0]?.init.method).toBe("POST");
    expect(requests[0]?.init.body).toBe('{"city":"Bucharest"}');
    expect((requests[0]?.init.headers as Record<string, string>)["x-request-id"]).toBe("req-7");
  });

  it("passes the service's 4xx through without retrying", async () => {
    const error = { status: 422, body: { error: { code: "invalid_window", message: "m", params: {} } } };
    const { client, requests } = harness([error]);

    const reply = await client.plan({}, {});

    expect(reply.status).toBe(422);
    expect(requests).toHaveLength(1);
  });

  it("retries a plan on 503 busy twice after Retry-After, then passes busy through", async () => {
    const { client, sleeps, requests } = harness([busy, busy, busy]);

    const reply = await client.plan({}, {});

    expect(reply.status).toBe(503);
    expect(reply.retryAfter).toBe("2");
    expect(requests).toHaveLength(3);
    expect(sleeps).toEqual([2000, 2000]);
  });

  it("serves a plan that frees up after busy", async () => {
    const { client } = harness([busy, ok]);

    expect((await client.plan({}, {})).status).toBe(200);
  });

  it("retries 503 not_ready once after its Retry-After", async () => {
    const notReady = {
      status: 503,
      body: { error: { code: "not_ready", message: "", params: {} } },
      retryAfter: "5"
    };
    const { client, sleeps } = harness([notReady, ok]);

    expect((await client.plan({}, {})).status).toBe(200);
    expect(sleeps).toEqual([5000]);
  });

  it("retries a refused connection once, then reports unavailable", async () => {
    const { client, sleeps } = harness([
      { error: "ECONNREFUSED" },
      { error: "ECONNREFUSED" }
    ]);

    await expect(client.plan({}, {})).rejects.toBeInstanceOf(
      WalkPlannerUnavailableError
    );
    expect(sleeps).toEqual([2000]);
  });

  it("treats an unresolvable host like a refused connection", async () => {
    const { client } = harness([{ error: "ENOTFOUND" }, ok]);

    expect((await client.config({})).status).toBe(200);
  });

  it("never re-sends a plan after a reset: the service may be computing it", async () => {
    const { client, requests } = harness([{ error: "ECONNRESET" }]);

    await expect(client.plan({}, {})).rejects.toBeInstanceOf(
      WalkPlannerUnavailableError
    );
    expect(requests).toHaveLength(1);
  });

  it("re-sends an edit once after a reset", async () => {
    const { client, requests } = harness([{ error: "ECONNRESET" }, ok]);

    expect((await client.schedule({}, {})).status).toBe(200);
    expect(requests).toHaveLength(2);
  });

  it("times out without retrying", async () => {
    vi.useFakeTimers();

    try {
      const { client, requests } = harness([{ hang: true }]);
      const pending = client.place("1", {});
      const assertion = expect(pending).rejects.toBeInstanceOf(
        WalkPlannerTimeoutError
      );

      await vi.advanceTimersByTimeAsync(5_000);
      await assertion;
      expect(requests).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not start a retry pause that would outlast the call deadline", async () => {
    // search: 5 s deadline; not_ready asks for 5 s → answered as is.
    const notReady = {
      status: 503,
      body: { error: { code: "not_ready", message: "", params: {} } },
      retryAfter: "5"
    };
    const { client, sleeps } = harness([notReady]);

    expect((await client.searchPlaces({ q: "a" })).status).toBe(503);
    expect(sleeps).toEqual([]);
  });

  it("is unavailable when WALK_PLANNER_URL is not set", async () => {
    const client = createWalkPlannerClient({ baseUrl: undefined });

    await expect(client.config({})).rejects.toBeInstanceOf(
      WalkPlannerUnavailableError
    );
  });
});
