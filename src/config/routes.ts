export const API_PREFIX = "/v1";

export enum AppRoute {
  Health = "/health",
  SupabaseHealth = "/health/supabase",
  Me = "/me",
  MeSaved = "/me/saved",
  MeSavedCollection = "/me/saved/collections/:collectionId",
  MeSavedPlaces = "/me/saved/places",
  MeSavedPlace = "/me/saved/places/:placeId",
  MeSavedCollections = "/me/saved/collections",
  MeSavedCollectionPlaces = "/me/saved/collections/:collectionId/places",
  MeSavedCollectionPlace = "/me/saved/collections/:collectionId/places/:placeId",
  MeSavedCollectionPlacesOrder = "/me/saved/collections/:collectionId/places/order",
  MapPlaces = "/map/places",
  SwaggerDocs = "/swagger/docs",
  SwaggerOpenApiJson = "/swagger/openapi.json"
}

export const VersionedAppRoute = {
  health: `${API_PREFIX}${AppRoute.Health}`,
  supabaseHealth: `${API_PREFIX}${AppRoute.SupabaseHealth}`,
  me: `${API_PREFIX}${AppRoute.Me}`,
  meSaved: `${API_PREFIX}${AppRoute.MeSaved}`,
  meSavedCollection: `${API_PREFIX}${AppRoute.MeSavedCollection}`,
  meSavedPlaces: `${API_PREFIX}${AppRoute.MeSavedPlaces}`,
  meSavedPlace: `${API_PREFIX}${AppRoute.MeSavedPlace}`,
  meSavedCollections: `${API_PREFIX}${AppRoute.MeSavedCollections}`,
  meSavedCollectionPlaces: `${API_PREFIX}${AppRoute.MeSavedCollectionPlaces}`,
  meSavedCollectionPlace: `${API_PREFIX}${AppRoute.MeSavedCollectionPlace}`,
  meSavedCollectionPlacesOrder: `${API_PREFIX}${AppRoute.MeSavedCollectionPlacesOrder}`,
  mapPlaces: `${API_PREFIX}${AppRoute.MapPlaces}`,
  swaggerDocs: `${API_PREFIX}${AppRoute.SwaggerDocs}`,
  swaggerOpenApiJson: `${API_PREFIX}${AppRoute.SwaggerOpenApiJson}`
} as const;
