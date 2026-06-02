import { buildComponentSchemas, makeDefineRoute } from "../../../config/openapi.js";
import { mapSchemaRegistry } from "./map.schemas.js";

// OpenAPI components generated from the zod registry (single source of truth).
export const mapComponentSchemas = buildComponentSchemas(mapSchemaRegistry);

// The map endpoint is public with optional auth, so no `security` is declared.
const defineRoute = makeDefineRoute({
  tag: "Map",
  security: false,
  errorResponses: {
    400: { $ref: "ValidationErrorResponse#" },
    401: { $ref: "AuthErrorResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
});

export const mapPlacesRouteSchema = defineRoute({
  summary: "Get places visible in a map bounding box.",
  description:
    "Used by the iOS map screen when the user opens the map or changes the visible region. The frontend sends the current map viewport as south-west and north-east coordinates. Coordinates are a bbox, not center/radius. The zoom determines the visibility score threshold; a pin is visible when its own mapVisibilityScore passes the zoom threshold. The optional limit is only a safety cap, not the normal density control. Results are lightweight place markers selected with best-effort spatial coverage when the cap is hit, not an exhaustive list. Use meta.capHit to detect clipping.",
  query: "MapPlacesQuery",
  ok: "MapPlacesResponse"
});
