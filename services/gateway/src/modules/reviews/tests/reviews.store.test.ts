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
});
