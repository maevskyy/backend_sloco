import { describe, expect, it } from "vitest";
import { buildApp } from "../../app.js";

describe("health routes", () => {
  it("returns ok status", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: "/health"
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      status: "ok"
    });
  });
});
