import { describe, expect, it } from "vitest";
import { buildApp } from "../app.js";
import { VersionedAppRoute } from "./routes.js";

describe("swagger docs", () => {
  it("serves Swagger UI under the v1 swagger namespace", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.swaggerDocs
    });

    await app.close();

    expect([200, 302]).toContain(response.statusCode);
  });

  it("serves an OpenAPI JSON contract for frontend agents", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.swaggerOpenApiJson
    });

    await app.close();

    expect(response.statusCode).toBe(200);

    const openApi = response.json();
    expect(openApi.openapi).toBeDefined();
    expect(openApi.info.title).toBe("Sloco Backend API");
    expect(openApi.paths[VersionedAppRoute.health]).toBeDefined();
    expect(openApi.paths[VersionedAppRoute.supabaseHealth]).toBeDefined();
    expect(openApi.paths[VersionedAppRoute.me]).toBeDefined();
    expect(openApi.paths[VersionedAppRoute.mapPlaces]).toBeDefined();
    expect(openApi.components.securitySchemes.bearerAuth).toBeDefined();
    expect(openApi.components.schemas.MeResponse).toBeDefined();
    expect(openApi.components.schemas.AuthErrorResponse).toBeDefined();
    expect(openApi.components.schemas.MapPlace).toBeDefined();
    expect(openApi.components.schemas.MapPlacesResponse).toBeDefined();
  });
});
