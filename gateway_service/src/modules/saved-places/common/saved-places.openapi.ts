import { buildComponentSchemas, makeDefineRoute } from "../../../config/openapi.js";
import { sharedErrorResponses } from "../../../config/http-schemas.js";
import { savedPlacesSchemaRegistry } from "./saved-places.schemas.js";

// OpenAPI components generated from the zod registry. zod is the single source of
// truth; nothing here is hand-written JSON Schema.
export const savedPlacesComponentSchemas = buildComponentSchemas(
  savedPlacesSchemaRegistry
);

// Route schemas. The shared `defineRoute` injects tags/security/error responses
// so each route is a single readable declaration.
const defineRoute = makeDefineRoute({
  tag: "SavedPlaces",
  errorResponses: sharedErrorResponses
});

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
