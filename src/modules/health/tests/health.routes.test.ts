import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";

describe("health routes", () => {
  it("returns ok status", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.health
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "ok"
    });
  });

  it("returns ok status for Supabase health when the check passes", async () => {
    const app = await buildApp({
      supabaseHealthCheck: async () => {}
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.supabaseHealth
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "ok"
    });
  });

  it("returns error status for Supabase health when the check fails", async () => {
    const app = await buildApp({
      supabaseHealthCheck: async () => {
        throw new Error("Supabase is unavailable");
      }
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.supabaseHealth
    });

    await app.close();

    expect(response.statusCode).toBe(500);
    expect(response.json()).toEqual({
      status: "error"
    });
  });

  it("does not expose unversioned health routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: AppRoute.Health
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
