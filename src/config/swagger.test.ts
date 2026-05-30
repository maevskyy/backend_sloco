import { describe, expect, it } from "vitest";
import { buildApp } from "../app.js";

describe("swagger docs", () => {
  it("serves Swagger UI under the v1 swagger namespace", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: "/v1/swagger/docs"
    });

    await app.close();

    expect([200, 302]).toContain(response.statusCode);
  });

  it("serves an OpenAPI JSON contract for frontend agents", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: "/v1/swagger/openapi.json"
    });

    await app.close();

    expect(response.statusCode).toBe(200);

    const openApi = response.json();
    expect(openApi.openapi).toBeDefined();
    expect(openApi.info.title).toBe("Sloco Backend API");
    expect(openApi.paths["/v1/health"]).toBeDefined();
    expect(openApi.paths["/v1/health/supabase"]).toBeDefined();
    expect(openApi.paths["/v1/map/places"]).toBeDefined();
    expect(openApi.components.schemas.MapPlace).toBeDefined();
    expect(openApi.components.schemas.MapPlacesResponse).toBeDefined();
  });
});
