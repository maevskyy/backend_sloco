import { z } from "zod";

// The coarse category vocabulary shared by /v1/search/places and
// /v1/feed/places (TASKS_45/46). The iOS client maps its chips onto these
// values; changing a name is a contract change.
//
// Keywords are matched WORD-BOUNDARY (SQL: ' '||col||' ' like '% '||kw||' %')
// against the normalized category / primary_type columns — substring matching
// would put "barbecue restaurant" into `bar`.
//
// The catalog's `types` array is deliberately NOT matched (migration 020): it is
// an attribute bag, not a taxonomy — `garden` there means "has a terrace", so
// matching it filed every restaurant with outdoor seating under `nature`.
// `primary_type` is the authoritative venue kind.
// Keyword choice is data-driven from the live catalog's primary_type
// distribution (12 578 places); `nature` and `shopping` are near-empty in the
// current two-city catalog — kept for contract stability, documented to iOS.
// Casinos/gambling/adult venues are deliberately in no bucket (mirrors the
// recommender's denylist); they stay reachable through text search.

export const PLACE_BUCKET_NAMES = [
  "cafe",
  "food",
  "bar",
  "culture",
  "nature",
  "shopping",
  "leisure"
] as const;

export type PlaceBucket = (typeof PLACE_BUCKET_NAMES)[number];

const PLACE_BUCKET_KEYWORDS: Record<PlaceBucket, readonly string[]> = {
  cafe: [
    "cafe",
    "coffee",
    "bakery",
    "patisserie",
    "pastry",
    "dessert",
    "confectionery",
    "sweets",
    "tea house"
  ],
  food: [
    "restaurant",
    "bistro",
    "buffet",
    "food",
    "diner",
    "grill",
    "steak",
    "burger",
    "hamburger",
    "pizza",
    "sushi",
    "shawarma",
    "gyro",
    "gastropub"
  ],
  bar: [
    "bar",
    "pub",
    "gastropub",
    "brewery",
    "brewpub",
    "taproom",
    "wine",
    "winery",
    "cocktail",
    "beer",
    "hookah"
  ],
  culture: [
    "museum",
    "gallery",
    "theater",
    "theatre",
    "concert",
    "opera",
    "cinema",
    "amphitheater",
    "arts",
    "cultural",
    "landmark",
    "monument",
    "historical",
    "church",
    "cathedral",
    "observatory",
    "planetarium",
    "exhibition",
    "circus"
  ],
  nature: [
    "park",
    "garden",
    "botanical",
    "nature",
    "zoo",
    "aquarium",
    "lake",
    "beach",
    "forest",
    "trail"
  ],
  shopping: [
    "shopping",
    "mall",
    "market",
    "bazaar",
    "boutique",
    "souvenir",
    "gift shop"
  ],
  leisure: [
    "amusement",
    "entertainment",
    "arcade",
    "escape room",
    "bowling",
    "billiard",
    "billard",
    "skating",
    "climbing",
    "karting",
    "laser tag",
    "paintball",
    "playground",
    "trampoline",
    "water park",
    "spa",
    "bath",
    "baths",
    "sauna",
    "night club",
    "dance club",
    "dance hall",
    "disco",
    "comedy club",
    "jazz club",
    "live music",
    "karaoke",
    "stadium",
    "arena",
    "gym",
    "fitness",
    "golf",
    "ferris",
    "recreation center"
  ]
};

// Accepts a repeated query param OR a single CSV value; validates every entry
// against the bucket enum (unknown value -> 400 via the zod error path).
export const placeBucketsQuerySchema = z
  .union([z.string(), z.array(z.string())])
  .transform((value) =>
    (Array.isArray(value) ? value : value.split(","))
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0)
  )
  .pipe(z.array(z.enum(PLACE_BUCKET_NAMES)).min(1))
  .optional();

export function bucketsToKeywords(buckets: readonly PlaceBucket[]): string[] {
  const keywords = new Set<string>();

  for (const bucket of buckets) {
    for (const keyword of PLACE_BUCKET_KEYWORDS[bucket]) {
      keywords.add(keyword);
    }
  }

  return [...keywords];
}

// The TS twin of the SQL word-boundary match, for filtering rows the gateway
// already holds (the personalized feed path). Callers pass the venue-kind
// fields (category, primary_type) — the same inputs the SQL uses, so both feed
// paths agree. `haystack` fields are plain ASCII English in the catalog, so
// lowercasing stands in for the SQL normalization.
export function matchesBucketKeywords(
  keywords: readonly string[],
  fields: ReadonlyArray<string | null | undefined>
): boolean {
  const haystack = ` ${fields
    .filter((field): field is string => Boolean(field))
    .join(" ")
    .toLowerCase()} `;

  return keywords.some((keyword) => haystack.includes(` ${keyword} `));
}
