import { describe, expect, it } from "vitest";
import {
  PLACE_BUCKET_NAMES,
  bucketsToKeywords,
  matchesBucketKeywords,
  placeBucketsQuerySchema
} from "../common/place-buckets.js";

describe("place buckets", () => {
  it("parses repeated params and CSV into validated bucket lists", () => {
    expect(placeBucketsQuerySchema.parse("bar,cafe")).toEqual(["bar", "cafe"]);
    expect(placeBucketsQuerySchema.parse(["culture"])).toEqual(["culture"]);
    expect(placeBucketsQuerySchema.parse(undefined)).toBeUndefined();
    expect(() => placeBucketsQuerySchema.parse("nightlife")).toThrow();
    expect(() => placeBucketsQuerySchema.parse("")).toThrow();
  });

  it("flattens buckets into a deduplicated keyword list", () => {
    const keywords = bucketsToKeywords(["food", "bar"]);

    expect(keywords).toContain("restaurant");
    expect(keywords).toContain("pub");
    // gastropub sits in both buckets and must appear once
    expect(keywords.filter((keyword) => keyword === "gastropub")).toHaveLength(1);
  });

  it("matches on word boundaries, not substrings", () => {
    const barKeywords = bucketsToKeywords(["bar"]);

    expect(matchesBucketKeywords(barKeywords, ["wine bar", null])).toBe(true);
    expect(matchesBucketKeywords(barKeywords, ["bar & grill"])).toBe(true);
    // the classic leak: "barbecue restaurant" is food, not a bar
    expect(matchesBucketKeywords(barKeywords, ["barbecue restaurant"])).toBe(false);

    const shoppingKeywords = bucketsToKeywords(["shopping"]);
    // "coffee shop" must not leak into shopping
    expect(matchesBucketKeywords(shoppingKeywords, ["coffee shop"])).toBe(false);
    expect(
      matchesBucketKeywords(shoppingKeywords, ["parklake shopping center"])
    ).toBe(true);
  });

  it("does not treat terrace/attribute words as a venue kind", () => {
    // Regression: the catalog's `types` bag carries `garden` meaning "has a
    // terrace", which filed restaurants under `nature` (migration 020).
    // Callers must pass venue-kind fields only.
    const natureKeywords = bucketsToKeywords(["nature"]);

    expect(matchesBucketKeywords(natureKeywords, ["Restaurant"])).toBe(false);
    expect(matchesBucketKeywords(natureKeywords, ["Gastropub"])).toBe(false);
    expect(matchesBucketKeywords(natureKeywords, ["Botanical garden"])).toBe(true);
  });

  it("keeps the seven-bucket contract stable", () => {
    expect(PLACE_BUCKET_NAMES).toEqual([
      "cafe",
      "food",
      "bar",
      "culture",
      "nature",
      "shopping",
      "leisure"
    ]);
  });
});
