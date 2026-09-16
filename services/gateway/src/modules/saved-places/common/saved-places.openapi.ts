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

export const setPlaceCollectionsRouteSchema = defineRoute({
  summary: "Set which lists a place belongs to.",
  description:
    "The save picker's write: makes the place's membership EXACTLY collectionIds. " +
    "Lists not in the array are left; lists already holding the place keep their " +
    "original savedAt order. An EMPTY array unsaves the place — it leaves every " +
    "list and the saved set, and the response carries isSaved=false. System lists " +
    "(slug saved | favorites | been) are ordinary targets here; they simply cannot " +
    "be deleted as lists.",
  params: "SavedPlaceParams",
  body: "SetPlaceCollectionsBody",
  ok: "PlaceCollectionsResponse"
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
