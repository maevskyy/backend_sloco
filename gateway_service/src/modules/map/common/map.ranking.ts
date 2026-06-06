export type MapRankingContext = {
  zoom?: number;
  userId?: string;
};

export type ScorablePlace = {
  source: string;
  source_id: string;
  latitude: number;
  longitude: number;
  rating: number | null;
  reviews_count: number | null;
  google_rating?: number | null;
  google_user_rating_count?: number | null;
  rating_score_0_100?: number | null;
  popularity_score_0_100?: number | null;
  map_visibility_score?: number | null;
  map_visibility_rank?: number | null;
};

export type MapViewportBbox = {
  swLat: number;
  swLng: number;
  neLat: number;
  neLng: number;
};

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 22;
export const MAP_PINS_SAFETY_CAP = 400;

const PERSONALIZATION_WEIGHT = 100;

export type MapVisibilityThresholds = {
  minScore: number;
};

/**
 * Calibrated from the first Bucharest serving dataset on 2026-06-02.
 * Recalibrate when city density grows, new cities are added, or capHit becomes
 * common in Grafana/logs.
 */
export function getMapVisibilityThresholds(
  zoom: number
): MapVisibilityThresholds {
  if (zoom <= 10) {
    return {
      minScore: 92
    };
  }

  if (zoom <= 12) {
    return {
      minScore: 86
    };
  }

  if (zoom <= 14) {
    return {
      minScore: 76
    };
  }

  if (zoom <= 16) {
    return {
      minScore: 66
    };
  }

  return {
    minScore: 56
  };
}

/**
 * Fallback when the frontend does not send `zoom`. The backend already receives
 * the viewport, so it can approximate a zoom from the visible longitude span.
 */
export function deriveZoomFromBbox(bbox: MapViewportBbox): number {
  const lngSpan = Math.max(bbox.neLng - bbox.swLng, 1e-6);
  const zoom = Math.log2(360 / lngSpan);

  return clampZoom(Math.floor(zoom));
}

export function getEffectiveMapZoom(bbox: MapViewportBbox, zoom?: number) {
  return zoom ?? deriveZoomFromBbox(bbox);
}

export function getMapPinsSafetyCap(requestedLimit: number | undefined) {
  return Math.min(requestedLimit ?? MAP_PINS_SAFETY_CAP, MAP_PINS_SAFETY_CAP);
}

/**
 * Non-personalized MVP score. `context` is the insertion point for the future
 * taste service; it currently contributes nothing.
 */
export function scoreMapPlace(
  place: ScorablePlace,
  context: MapRankingContext
): number {
  const mapVisibilityScore = place.map_visibility_score ?? 0;
  const ratingScore =
    place.rating_score_0_100 ??
    (place.google_rating ?? place.rating ?? 0) * 10;
  const popularityScore = place.popularity_score_0_100 ?? 0;
  const reviews = place.google_user_rating_count ?? place.reviews_count ?? 0;
  const reviewsScore =
    reviews > 0 ? Math.min(Math.log10(reviews + 1) * 5, 20) : 0;
  const sourceScore = place.source === "google" ? 5 : 0;
  const jitter = stableJitter(place.source_id);

  return (
    mapVisibilityScore * 2 +
    ratingScore +
    popularityScore * 0.4 +
    reviewsScore +
    sourceScore +
    jitter +
    tasteScore(context)
  );
}

export function rankMapPlaces<T extends ScorablePlace>(
  places: T[],
  context: MapRankingContext,
  limit: number
): T[] {
  return places
    .map((place) => ({ place, score: scoreMapPlace(place, context) }))
    .sort((a, b) => b.score - a.score)
    .slice(0, Math.max(limit, 0))
    .map((entry) => entry.place);
}

/**
 * Personalization hook. Returns 0 until the Python taste service is wired in,
 * at which point it will weight by context.userId.
 */
function tasteScore(context: MapRankingContext): number {
  const userTasteScore = 0;

  return context.userId ? userTasteScore * PERSONALIZATION_WEIGHT : 0;
}

function clampZoom(zoom: number): number {
  if (zoom < MIN_ZOOM) {
    return MIN_ZOOM;
  }

  if (zoom > MAX_ZOOM) {
    return MAX_ZOOM;
  }

  return zoom;
}

/**
 * Stable 0..1 pseudo-jitter from the source id. Breaks score ties without
 * sorting by DB id, and stays identical between requests.
 */
function stableJitter(sourceId: string): number {
  let hash = 0;

  for (let i = 0; i < sourceId.length; i += 1) {
    hash = (hash * 31 + sourceId.charCodeAt(i)) >>> 0;
  }

  return (hash % 1000) / 1000;
}
