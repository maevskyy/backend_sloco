import type {
  PlaceDetailRow,
  PlaceDetails,
  PlacePhotoRow
} from "./places.types.js";

export function mapPlaceDetailRow(
  row: PlaceDetailRow,
  photos: PlacePhotoRow[] = []
): PlaceDetails {
  return {
    id: row.id,
    source: row.source,
    sourceId: row.source_id,
    name: row.name,
    country: row.country,
    city: row.city,
    category: row.category,
    primaryType: row.primary_type,
    types: row.types ?? [],
    latitude: row.latitude,
    longitude: row.longitude,
    formattedAddress: row.formatted_address,
    shortFormattedAddress: row.short_formatted_address,
    businessStatus: row.business_status,
    googleMapsUri: row.google_maps_uri,
    phone: row.phone,
    internationalPhone: row.international_phone,
    websiteUrl: row.website_url,
    rating: row.rating,
    priceLevel: row.price_level,
    numberOfReviews: row.reviews_count,
    googleRating: row.google_rating,
    googleUserRatingCount: row.google_user_rating_count,
    apifyReviewCount: row.apify_review_count,
    apifyRatingAvg: row.apify_rating_avg,
    ratingCountForScore: row.rating_count_for_score,
    bayesianRating: row.bayesian_rating,
    ratingScore: row.rating_score_0_100,
    popularityScore: row.popularity_score_0_100,
    ratingConfidenceScore: row.rating_confidence_0_100,
    priceMinRon: row.price_min_ron,
    priceMaxRon: row.price_max_ron,
    mapVisibilityScore: row.map_visibility_score ?? 0,
    mapVisibilityRank: row.map_visibility_rank,
    mapMinZoomGlobal: row.map_min_zoom_global,
    aiCardSummary: row.ai_card_summary,
    aiPlaceTypeSummary: row.ai_place_type_summary,
    aiVibe: row.ai_vibe,
    aiWhatToExpect: row.ai_what_to_expect,
    aiFoodAndDrinks: row.ai_food_and_drinks,
    aiPrice: row.ai_price,
    aiService: row.ai_service,
    aiTheMove: row.ai_the_move,
    aiWatchOut: row.ai_watch_out,
    aiTags: row.ai_tags ?? [],
    aiTagsJson: row.ai_tags_json ?? null,
    aiConfidence: row.ai_confidence,
    axisQuietLively: row.axis_quiet_lively,
    axisWorkSocial: row.axis_work_social,
    axisDayNight: row.axis_day_night,
    axisCasualPremium: row.axis_casual_premium,
    axisDrinksFood: row.axis_drinks_food,
    axisLocalTourist: row.axis_local_tourist,
    axisCheapExpensive: row.axis_cheap_expensive,
    axisTraditionalExperimental: row.axis_traditional_experimental,
    reviewPhotoCount: row.review_photo_count ?? 0,
    vibePhotoCount: row.vibe_photo_count ?? 0,
    primaryPhoto: mapPrimaryPhoto(row),
    photos: photos.map(mapPhotoRow),
    totalPhotoCount: row.total_photo_count ?? 0,
    openingHours: row.opening_hours ?? null,
    serves: row.serves ?? null,
    features: row.features ?? null,
    googleDetails: row.google_details,
    apifyDetails: row.apify_details,
    aiDetails: row.ai_details,
    photoDetails: row.photo_details,
    rawCuisineStyle:
      getStringAttribute(row.attributes, "raw_cuisine_style") ??
      getStringAttribute(row.attributes, "cuisine"),
    isSaved: false,
    savedCollectionIds: [],
    reaction: null
  };
}

function mapPrimaryPhoto(row: PlaceDetailRow): PlaceDetails["primaryPhoto"] {
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

function mapPhotoRow(photo: PlacePhotoRow): NonNullable<PlaceDetails["photos"]>[number] {
  return {
    path: photo.storage_path,
    url: photo.public_url,
    width: photo.width,
    height: photo.height,
    source: photo.photo_source
  };
}

function getStringAttribute(
  attributes: Record<string, unknown> | null,
  key: string
) {
  const value = attributes?.[key];

  return typeof value === "string" && value.trim().length > 0 ? value : null;
}
