import { buildComponentSchemas } from "../../../config/openapi.js";
import { healthSchemaRegistry } from "./health.schemas.js";

export const healthComponentSchemas = buildComponentSchemas(healthSchemaRegistry);

// Health routes are simple and have distinct response sets, so they are declared
// directly rather than via the shared `defineRoute` helper.
export const healthRouteSchema = {
  tags: ["Health"],
  summary: "Check backend health.",
  description: "Returns ok when the backend process is running.",
  response: {
    200: { $ref: "HealthStatusResponse#" }
  }
} as const;

export const supabaseHealthRouteSchema = {
  tags: ["Health"],
  summary: "Check Supabase health.",
  description: "Returns ok when the backend can read from Supabase.",
  response: {
    200: { $ref: "HealthStatusResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
} as const;
