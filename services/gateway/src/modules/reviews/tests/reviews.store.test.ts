import { describe, expect, it } from "vitest";
import type { Db, DbQueryResult } from "../../../lib/db.js";
import { PlaceNotFoundError, ReviewsStore, type ReviewRow } from "../index.js";

const userId = "0f70a78a-05f8-45da-81b5-a435fdadf16c";

type Statement = { text: string; params: readonly unknown[] };

function createFakeDb(respond: (text: string) => Record<string, unknown>[]) {
  const statements: Statement[] = [];

  const db: Db = {
    async query<T>(text: string, params: readonly unknown[] = []) {
      statements.push({ text, params });
      const rows = respond(text) as T[];
      return { rows, rowCount: rows.length } as DbQueryResult<T>;
    },
    async transaction() {
      throw new Error("reviews store does not use transactions");
    }
  };

  return { db, statements };
}

const row: ReviewRow = {
  place_id: 6124,
  place_name: "Random Space",
  place_rating: 4.7,
  place_reviews_count: 160,
  primary_photo_path: null,
  primary_photo_url: null,
  primary_photo_width: null,
  primary_photo_height: null,
  primary_photo_source: null,
  rating: 4,
  body: "ok",
  tags: ["cozy"],
  helpful_count: 0,
  created_at: "2026-10-06T10:00:00.000000+00:00",
  updated_at: "2026-10-06T10:00:00.000000+00:00"
};

describe("reviews store", () => {
  it("upserts in one statement and returns the joined row", async () => {
    const fake = createFakeDb(() => [row]);
    const store = new ReviewsStore(fake.db);

    const result = await store.upsertReview(userId, 6124, {
      rating: 4,
      text: "ok",
      tags: ["cozy"]
    });

    expect(result).toEqual(row);
    expect(fake.statements).toHaveLength(1);
    expect(fake.statements[0]!.params).toEqual([userId, 6124, 4, "ok", ["cozy"]]);
    // A replace must not touch created_at or helpful_count.
    const conflictClause = fake.statements[0]!.text
      .split("do update")[1]!
      .split("returning")[0]!;
    expect(conflictClause).not.toMatch(/created_at|helpful_count/);
  });

  it("throws PlaceNotFoundError when the upsert finds no place", async () => {
    const store = new ReviewsStore(createFakeDb(() => []).db);

    await expect(
      store.upsertReview(userId, 999999, { rating: 4, text: "", tags: [] })
    ).rejects.toBeInstanceOf(PlaceNotFoundError);
  });

  it("deletes idempotently but 404s on an unknown place", async () => {
    const known = new ReviewsStore(
      createFakeDb(() => [{ place_exists: true }]).db
    );
    const unknown = new ReviewsStore(
      createFakeDb(() => [{ place_exists: false }]).db
    );

    await expect(known.deleteReview(userId, 6124)).resolves.toBeUndefined();
    await expect(unknown.deleteReview(userId, 999999)).rejects.toBeInstanceOf(
      PlaceNotFoundError
    );
  });

  it("takes the total from the window count without a second query", async () => {
    const fake = createFakeDb(() => [{ ...row, total: 19 }]);
    const store = new ReviewsStore(fake.db);

    const page = await store.listReviews(userId, { limit: 1, offset: 0 });

    expect(page.total).toBe(19);
    expect(page.rows).toHaveLength(1);
    expect(page.rows[0]).toMatchObject(row);
    expect(fake.statements).toHaveLength(1);
    expect(fake.statements[0]!.params).toEqual([userId, 1, 0]);
  });

  it("counts separately only for an empty page past the start", async () => {
    const first = createFakeDb(() => []);
    const past = createFakeDb((text) =>
      /count\(\*\) as total/.test(text) ? [{ total: 3 }] : []
    );

    await expect(
      new ReviewsStore(first.db).listReviews(userId, { limit: 50, offset: 0 })
    ).resolves.toEqual({ rows: [], total: 0 });
    expect(first.statements).toHaveLength(1);

    await expect(
      new ReviewsStore(past.db).listReviews(userId, { limit: 50, offset: 50 })
    ).resolves.toEqual({ rows: [], total: 3 });
    expect(past.statements).toHaveLength(2);
  });

  describe("listPlaceReviews", () => {
    const reviewed = {
      author_display_name: "Veronika Ignatenko",
      is_mine: false,
      rating: 5,
      body: "A really nice place!",
      tags: ["romantic"],
      helpful_count: 0,
      created_at: "2026-10-06T10:00:00.000000+00:00",
      updated_at: "2026-10-06T10:00:00.000000+00:00"
    };
    // What the left join yields for a place without reviews.
    const unreviewed = {
      author_display_name: null,
      is_mine: false,
      rating: null,
      body: null,
      tags: null,
      helpful_count: null,
      created_at: null,
      updated_at: null,
      total: 0
    };

    it("reads a page and its total in one statement", async () => {
      const fake = createFakeDb(() => [{ ...reviewed, total: 7 }]);

      const page = await new ReviewsStore(fake.db).listPlaceReviews(
        6124,
        userId,
        { limit: 20, offset: 0 }
      );

      expect(page.total).toBe(7);
      expect(page.rows).toHaveLength(1);
      expect(page.rows[0]).toMatchObject(reviewed);
      expect(fake.statements).toHaveLength(1);
      expect(fake.statements[0]!.params).toEqual([6124, userId, 20, 0]);
      // The author's id is compared in SQL and never selected.
      expect(fake.statements[0]!.text).not.toMatch(/r\.user_id as|email/);
    });

    it("passes a null viewer for an anonymous caller", async () => {
      const fake = createFakeDb(() => [{ ...reviewed, total: 1 }]);

      await new ReviewsStore(fake.db).listPlaceReviews(6124, null, {
        limit: 20,
        offset: 0
      });

      expect(fake.statements[0]!.params).toEqual([6124, null, 20, 0]);
    });

    it("turns the place's no-reviews row into an empty page", async () => {
      const fake = createFakeDb(() => [unreviewed]);

      await expect(
        new ReviewsStore(fake.db).listPlaceReviews(6124, null, {
          limit: 20,
          offset: 0
        })
      ).resolves.toEqual({ rows: [], total: 0 });
      expect(fake.statements).toHaveLength(1);
    });

    it("throws PlaceNotFoundError when the first page has no rows", async () => {
      const fake = createFakeDb(() => []);

      await expect(
        new ReviewsStore(fake.db).listPlaceReviews(999999999, null, {
          limit: 20,
          offset: 0
        })
      ).rejects.toBeInstanceOf(PlaceNotFoundError);
      expect(fake.statements).toHaveLength(1);
    });

    it("counts separately for an empty page past the end", async () => {
      const known = createFakeDb((text) =>
        /place_exists/.test(text) ? [{ place_exists: true, total: 3 }] : []
      );
      const unknown = createFakeDb((text) =>
        /place_exists/.test(text) ? [{ place_exists: false, total: 0 }] : []
      );

      await expect(
        new ReviewsStore(known.db).listPlaceReviews(6124, null, {
          limit: 20,
          offset: 20
        })
      ).resolves.toEqual({ rows: [], total: 3 });
      expect(known.statements).toHaveLength(2);

      await expect(
        new ReviewsStore(unknown.db).listPlaceReviews(999999999, null, {
          limit: 20,
          offset: 20
        })
      ).rejects.toBeInstanceOf(PlaceNotFoundError);
    });
  });
});
