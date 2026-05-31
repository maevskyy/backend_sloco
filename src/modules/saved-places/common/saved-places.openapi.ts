import { z } from "zod";
import { savedPlacesSchemaRegistry } from "./saved-places.schemas.js";

// ---------------------------------------------------------------------------
// OpenAPI components, generated from the zod registry in saved-places.schemas.
// zod is the single source of truth; nothing here is hand-written JSON Schema.
// ---------------------------------------------------------------------------

const generated = z.toJSONSchema(savedPlacesSchemaRegistry, {
  uri: (id) => `${id}#`,
  unrepresentable: "any", // `.refine()` on update body is not representable
  target: "openapi-3.0" // matches the 3.0.3 doc emitted by @fastify/swagger
});

// Normalize for Fastify: `$id` is the bare component name (refs stay `Name#`).
export const savedPlacesComponentSchemas = Object.entries(generated.schemas).map(
  ([id, schema]) => ({ ...(schema as Record<string, unknown>), $id: id })
);

// ---------------------------------------------------------------------------
// Route schemas. Shared `tags`/`security`/error responses live in one helper
// so each route is a single readable line.
// ---------------------------------------------------------------------------

const errorResponses = {
  400: { $ref: "ValidationErrorResponse#" },
  401: { $ref: "AuthErrorResponse#" },
  404: { $ref: "NotFoundResponse#" },
  409: { $ref: "ErrorResponse#" },
  500: { $ref: "ErrorResponse#" }
} as const;

function defineRoute(opts: {
  summary: string;
  description?: string;
  params?: string;
  body?: string;
  ok: string;
}) {
  return {
    tags: ["SavedPlaces"],
    summary: opts.summary,
    ...(opts.description ? { description: opts.description } : {}),
    security: [{ bearerAuth: [] }],
    ...(opts.params ? { params: { $ref: `${opts.params}#` } } : {}),
    ...(opts.body ? { body: { $ref: `${opts.body}#` } } : {}),
    response: {
      200: { $ref: `${opts.ok}#` },
      ...errorResponses
    }
  } as const;
}

export const getSavedDashboardRouteSchema = defineRoute({
  summary: "Get saved dashboard.",
  description:
    "Returns summary, collections, and recently saved places for the authenticated user.",
  ok: "SavedDashboardResponse"
});

export const getSavedCollectionRouteSchema = defineRoute({
  summary: "Get saved collection detail.",
  params: "SavedCollectionParams",
  ok: "SavedCollectionDetailResponse"
});

export const savePlaceRouteSchema = defineRoute({
  summary: "Save a place.",
  body: "SavePlaceBody",
  ok: "SavePlaceResponse"
});

export const unsavePlaceRouteSchema = defineRoute({
  summary: "Unsave a place.",
  params: "SavedPlaceParams",
  ok: "UnsavePlaceResponse"
});

export const createCollectionRouteSchema = defineRoute({
  summary: "Create saved collection.",
  body: "SavedCollectionBody",
  ok: "SavedCollectionResponse"
});

export const updateCollectionRouteSchema = defineRoute({
  summary: "Update saved collection.",
  params: "SavedCollectionParams",
  body: "UpdateSavedCollectionBody",
  ok: "SavedCollectionResponse"
});

export const deleteCollectionRouteSchema = defineRoute({
  summary: "Delete saved collection.",
  params: "SavedCollectionParams",
  ok: "DeleteSavedCollectionResponse"
});

export const addPlaceToCollectionRouteSchema = defineRoute({
  summary: "Add place to saved collection.",
  params: "SavedCollectionParams",
  body: "AddPlaceToCollectionBody",
  ok: "SavePlaceResponse"
});

export const removePlaceFromCollectionRouteSchema = defineRoute({
  summary: "Remove place from saved collection.",
  params: "SavedCollectionPlaceParams",
  ok: "RemovePlaceFromCollectionResponse"
});

export const reorderCollectionPlacesRouteSchema = defineRoute({
  summary: "Reorder places in saved collection.",
  params: "SavedCollectionParams",
  body: "ReorderCollectionPlacesBody",
  ok: "ReorderCollectionPlacesResponse"
});
