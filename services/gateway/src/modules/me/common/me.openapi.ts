import { buildComponentSchemas, makeDefineRoute } from "../../../config/openapi.js";
import { meSchemaRegistry } from "./me.schemas.js";

// OpenAPI components generated from the zod registry (single source of truth).
export const meComponentSchemas = buildComponentSchemas(meSchemaRegistry);

const defineRoute = makeDefineRoute({
  tag: "Me",
  errorResponses: {
    401: { $ref: "AuthErrorResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
});

export const meRouteSchema = defineRoute({
  summary: "Get the authenticated user and profile.",
  description:
    "Verifies a Supabase Auth access token and returns the current user with their backend profile.",
  ok: "MeResponse"
});

export const meSavedIdsRouteSchema = defineRoute({
  summary: "Get ids of places saved by the authenticated user.",
  description:
    "Used by vector-tile map clients to apply saved state with Mapbox feature-state. Tiles stay public and cacheable; user-specific state is fetched separately.",
  ok: "MeSavedIdsResponse"
});
