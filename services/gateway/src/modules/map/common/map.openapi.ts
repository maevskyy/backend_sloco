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

export const mapConfigRouteSchema = defineRoute({
  summary: "Get map vector-tile client configuration.",
  description:
    "Returns the current tile data version, vector tile URL template, and MVT source layer name. The frontend uses tileVersion as the cache-busting DATA_VERSION.",
  ok: "MapConfigResponse"
});

export const mapTileRouteSchema = {
  tags: ["Map"],
  summary: "Get a Mapbox Vector Tile for places.",
  description:
    "Returns a binary Mapbox Vector Tile (.mvt) for the requested z/x/y tile. The source layer is `places`, feature id is the backend place id, and feature properties are lightweight map attributes. Saved state is not embedded in tiles; use /v1/me/saved/ids and Mapbox feature-state on the client.",
  params: {
    type: "object",
    required: ["z", "x", "y"],
    properties: {
      z: {
        type: "integer",
        minimum: 1,
        maximum: 22
      },
      x: {
        type: "integer",
        minimum: 0
      },
      y: {
        type: "string",
        pattern: "^\\d+\\.mvt$",
        description:
          "The Fastify route captures this as the numeric y param before the .mvt suffix; clients still call /{y}.mvt."
      }
    }
  },
  response: {
    200: {
      description: "Binary Mapbox Vector Tile.",
      type: "string",
      format: "binary"
    },
    204: {
      description: "The tile has no visible places."
    },
    304: {
      description: "The tile version has not changed."
    },
    400: { $ref: "ValidationErrorResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
} as const;
