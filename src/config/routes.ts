export const API_PREFIX = "/v1";

export enum AppRoute {
  Health = "/health",
  SupabaseHealth = "/health/supabase",
  Me = "/me",
  MapPlaces = "/map/places",
  SwaggerDocs = "/swagger/docs",
  SwaggerOpenApiJson = "/swagger/openapi.json"
}

export const VersionedAppRoute = {
  health: `${API_PREFIX}${AppRoute.Health}`,
  supabaseHealth: `${API_PREFIX}${AppRoute.SupabaseHealth}`,
  me: `${API_PREFIX}${AppRoute.Me}`,
  mapPlaces: `${API_PREFIX}${AppRoute.MapPlaces}`,
  swaggerDocs: `${API_PREFIX}${AppRoute.SwaggerDocs}`,
  swaggerOpenApiJson: `${API_PREFIX}${AppRoute.SwaggerOpenApiJson}`
} as const;
