import type {
  FeedPersonalizationStatus,
  FeedPlaceCard,
  FeedPlaceRow,
  FeedRecommendationSeed
} from "./feed.types.js";

type FeedMapContext = {
  status: FeedPersonalizationStatus;
  recommendation?: FeedRecommendationSeed;
  rank: number;
  // Snapshot position for telemetry (kept separate from `recommendation`, which
  // category/city cuts drop to make rank positional). Null on fallback feeds.
  position?: number | null;
};

export function mapFeedRowToCard(
  row: FeedPlaceRow,
  context: FeedMapContext
): FeedPlaceCard {
  return {
    id: row.id,
    source: row.source,
    sourceId: row.source_id,
    name: row.name,
    country: row.country,
    city: row.city,
    category: row.category,
    primaryType: row.primary_type,
    latitude: row.latitude,
    longitude: row.longitude,
    rating: row.rating,
    priceLevel: row.price_level,
    numberOfReviews: row.reviews_count,
    mapVisibilityScore: Number(row.map_visibility_score ?? 0),
    matchScore: getMatchScore(row, context.recommendation),
    rank: getRank(context),
    position: context.position ?? null,
    whyRecommended: getWhyRecommended(row, context.status),
    blurb: getBlurb(row),
    tags: getTags(row),
    distanceMeters: row.distance_m,
    primaryPhoto: mapPrimaryPhoto(row),
    isSaved: false,
    reaction: null
  };
}

function getRank(context: FeedMapContext) {
  return context.recommendation?.rank ?? context.rank;
}

function getMatchScore(
  row: FeedPlaceRow,
  recommendation: FeedRecommendationSeed | undefined
) {
  if (recommendation) {
    return normalizeScore(recommendation.score);
  }

  return normalizeScore(Number(row.map_visibility_score ?? 0));
}

function normalizeScore(score: number) {
  if (!Number.isFinite(score)) return 0;

  const normalized = score <= 1 ? score * 100 : score;
  return Math.round(Math.min(100, Math.max(0, normalized)));
}

function getWhyRecommended(
  row: FeedPlaceRow,
  status: FeedPersonalizationStatus
) {
  if (status === "personalized") {
    return row.ai_the_move ?? "Because this matches places you saved.";
  }

  if (status === "anonymous_fallback") {
    return "A strong city pick to start exploring.";
  }

  if (status === "no_signals_fallback") {
    return "Save a few places and this feed will become more personal.";
  }

  return "A strong fallback pick while personalization is warming up.";
}

function getBlurb(row: FeedPlaceRow) {
  return (
    row.ai_card_summary ??
    row.ai_place_type_summary ??
    row.ai_vibe ??
    row.formatted_address ??
    ""
  );
}

function getTags(row: FeedPlaceRow) {
  const tags = (row.ai_tags ?? [])
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 4);

  if (tags.length > 0) return tags;

  return [row.primary_type ?? row.category].filter(Boolean).slice(0, 1);
}

function mapPrimaryPhoto(row: FeedPlaceRow): FeedPlaceCard["primaryPhoto"] {
  if (!row.primary_photo_path) {
    return null;
  }

  return {
    path: row.primary_photo_path,
    url: row.primary_photo_url,
    width: row.primary_photo_width,
    height: row.primary_photo_height,
    source: row.primary_photo_source
  };
}
