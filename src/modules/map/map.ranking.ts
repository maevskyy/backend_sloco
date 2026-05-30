export type MapRankingContext = {
  zoom?: number;
  city: string;
  userId?: string;
};

export type ScorablePlace = {
  source: string;
  source_id: string;
  rating: number | null;
  reviews_count: number | null;
};

export type MapViewportBbox = {
  swLat: number;
  swLng: number;
  neLat: number;
  neLng: number;
};

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 22;

const CANDIDATE_OVERFETCH = 4;
const MAX_CANDIDATES = 400;
const PERSONALIZATION_WEIGHT = 100;

/**
 * Max individual places to show for a given zoom level. Intentionally
 * conservative: there is no clustering yet, so this density cap is the only
 * declutter mechanism. See docs/tasks/TASKS_13_MAP_DENSITY_RANKING.md.
 */
export function getMapDensityLimit(zoom: number): number {
  if (zoom < 11) {
    return 8;
  }

  if (zoom <= 12) {
    return 15;
  }

  if (zoom <= 14) {
    return 25;
  }

  return 40;
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

export function getDensityLimit(
  bbox: MapViewportBbox,
  zoom?: number
): number {
  const effectiveZoom = zoom ?? deriveZoomFromBbox(bbox);

  return getMapDensityLimit(effectiveZoom);
}

/**
 * The user/debug `limit` can only narrow the density cap, never widen it.
 */
export function getEffectiveLimit(
  userLimit: number | undefined,
  densityLimit: number
): number {
  return Math.min(userLimit ?? densityLimit, densityLimit);
}

/**
 * Overfetch from the database so the scorer has room to choose the best rows.
 */
export function getCandidateLimit(effectiveLimit: number): number {
  return Math.min(effectiveLimit * CANDIDATE_OVERFETCH, MAX_CANDIDATES);
}

/**
 * Non-personalized MVP score. `context` is the insertion point for the future
 * taste service; it currently contributes nothing.
 */
export function scoreMapPlace(
  place: ScorablePlace,
  context: MapRankingContext
): number {
  const ratingScore = place.rating !== null ? place.rating * 10 : 0;
  const reviews = place.reviews_count ?? 0;
  const reviewsScore =
    reviews > 0 ? Math.min(Math.log10(reviews + 1) * 5, 20) : 0;
  const sourceScore = place.source === "tripadvisor" ? 5 : 0;
  const jitter = stableJitter(place.source_id);

  return ratingScore + reviewsScore + sourceScore + jitter + tasteScore(context);
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
