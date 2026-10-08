import { getDb, type Db } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import { PlaceNotFoundError } from "../common/reviews.errors.js";
import type {
  ListPlaceReviewsQuery,
  ListReviewsQuery
} from "../common/reviews.schemas.js";
import type {
  PlaceReviewRow,
  PlaceReviewRowsPage,
  ReviewInput,
  ReviewRow,
  ReviewRowsPage,
  ReviewsStoreContract
} from "../common/reviews.types.js";

type Nullable<T> = { [K in keyof T]: T[K] | null };

function isReviewed<T extends Nullable<PlaceReviewRow>>(
  row: T
): row is T & PlaceReviewRow {
  return row.rating !== null;
}

function measureReviewsDependency<T>(
  operation: string,
  name: string,
  callback: () => Promise<T>,
  getRowsCount?: (result: T) => number | undefined
) {
  return measureDependencyMetric(
    {
      dependency: "postgres",
      operation,
      name
    },
    callback,
    getRowsCount
  );
}

// Reviews are keyed by the place's stable (source, source_id), the API by
// places.id. `r` is a place_reviews row; this joins its place card and the
// primary photo the same way place_details_by_id does (migration 010).
const REVIEW_COLUMNS = `p.id as place_id,
       p.name as place_name,
       p.rating as place_rating,
       p.reviews_count as place_reviews_count,
       p.primary_photo_path,
       ph.public_url as primary_photo_url,
       ph.width as primary_photo_width,
       ph.height as primary_photo_height,
       ph.photo_source as primary_photo_source,
       r.rating,
       r.body,
       r.tags,
       r.helpful_count,
       r.created_at,
       r.updated_at`;

const PLACE_JOIN = `join public.places p
    on p.source = r.place_source
   and p.source_id = r.place_source_id
  left join public.place_photos ph
    on ph.place_source = p.source
   and ph.place_source_id = p.source_id
   and ph.storage_path = p.primary_photo_path`;

export class ReviewsStore implements ReviewsStoreContract {
  constructor(private readonly db: Db = getDb()) {}

  async upsertReview(userId: string, placeId: number, input: ReviewInput) {
    // One round trip: resolve the place, upsert, and read the card back.
    // No place → the insert selects nothing → no row → 404. A replace keeps
    // created_at and helpful_count; on insert both timestamps are the same
    // now() of this statement.
    const result = await measureReviewsDependency(
      "upsert",
      "place_reviews_upsert",
      async () =>
        this.db.query<ReviewRow>(
          `with target as (
             select source, source_id
               from public.places
              where id = $2
           ), r as (
             insert into public.place_reviews
               (user_id, place_source, place_source_id, rating, body, tags)
             select $1, source, source_id, $3, $4, $5::text[]
               from target
             on conflict (user_id, place_source, place_source_id) do update
               set rating = excluded.rating,
                   body = excluded.body,
                   tags = excluded.tags,
                   updated_at = now()
             returning *
           )
           select ${REVIEW_COLUMNS}
             from r
             ${PLACE_JOIN}`,
          [userId, placeId, input.rating, input.text, input.tags]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );
    const row = result.rows[0];

    if (!row) {
      throw new PlaceNotFoundError(placeId);
    }

    return row;
  }

  async deleteReview(userId: string, placeId: number) {
    // A data-modifying CTE runs even though the outer select ignores it.
    const result = await measureReviewsDependency(
      "delete",
      "place_reviews_delete",
      async () =>
        this.db.query<{ place_exists: boolean }>(
          `with target as (
             select source, source_id
               from public.places
              where id = $2
           ), deleted as (
             delete from public.place_reviews r
              using target t
              where r.user_id = $1
                and r.place_source = t.source
                and r.place_source_id = t.source_id
           )
           select exists (select 1 from target) as place_exists`,
          [userId, placeId]
        )
    );

    if (!result.rows[0]?.place_exists) {
      throw new PlaceNotFoundError(placeId);
    }
  }

  async listReviews(
    userId: string,
    page: ListReviewsQuery
  ): Promise<ReviewRowsPage> {
    // Inner join: a review whose place left the catalog is neither listed
    // nor counted.
    const result = await measureReviewsDependency(
      "select",
      "place_reviews_list",
      async () =>
        this.db.query<ReviewRow & { total: number }>(
          `select ${REVIEW_COLUMNS},
                  count(*) over () as total
             from public.place_reviews r
             ${PLACE_JOIN}
            where r.user_id = $1
            order by r.created_at desc, p.id desc
            limit $2
           offset $3`,
          [userId, page.limit, page.offset]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    const first = result.rows[0];

    if (first) {
      // `total` rides along on each row; mapReviewRow ignores it.
      return {
        rows: result.rows,
        total: first.total
      };
    }

    // An empty page carries no window count. On the first page that means
    // zero; past the end it takes one more query.
    return {
      rows: [],
      total: page.offset === 0 ? 0 : await this.countReviews(userId)
    };
  }

  async listPlaceReviews(
    placeId: number,
    viewerId: string | null,
    page: ListPlaceReviewsQuery
  ): Promise<PlaceReviewRowsPage> {
    // The place left-joins its reviews, so a place without reviews still
    // yields one all-null row and "no rows" on the first page means "no
    // place". count(r.user_id) skips that null row. A seq scan of
    // place_reviews for now: no index by place until the table grows
    // (docs/DECISIONS.md).
    const result = await measureReviewsDependency(
      "select",
      "place_reviews_by_place_list",
      async () =>
        this.db.query<Nullable<PlaceReviewRow> & { total: number }>(
          `with target as (
             select source, source_id
               from public.places
              where id = $1
           )
           select pr.display_name as author_display_name,
                  coalesce(r.user_id = $2::uuid, false) as is_mine,
                  r.rating,
                  r.body,
                  r.tags,
                  r.helpful_count,
                  r.created_at,
                  r.updated_at,
                  count(r.user_id) over () as total
             from target t
             left join public.place_reviews r
               on r.place_source = t.source
              and r.place_source_id = t.source_id
             left join public.profiles pr
               on pr.user_id = r.user_id
            order by r.created_at desc, r.user_id
            limit $3
           offset $4`,
          [placeId, viewerId, page.limit, page.offset]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    const first = result.rows[0];

    if (first) {
      return {
        // Only the no-reviews row has a null rating.
        rows: result.rows.filter(isReviewed),
        total: first.total
      };
    }

    if (page.offset === 0) {
      throw new PlaceNotFoundError(placeId);
    }

    // Past the end of the list: one more query tells "no place" from "no
    // more reviews" and counts them.
    const { placeExists, total } = await this.countPlaceReviews(placeId);

    if (!placeExists) {
      throw new PlaceNotFoundError(placeId);
    }

    return {
      rows: [],
      total
    };
  }

  private async countPlaceReviews(placeId: number) {
    const result = await measureReviewsDependency(
      "select",
      "place_reviews_by_place_count",
      async () =>
        this.db.query<{ place_exists: boolean; total: number }>(
          `with target as (
             select source, source_id
               from public.places
              where id = $1
           )
           select exists (select 1 from target) as place_exists,
                  (select count(*)
                     from public.place_reviews r
                     join target t
                       on r.place_source = t.source
                      and r.place_source_id = t.source_id) as total`,
          [placeId]
        )
    );
    const row = result.rows[0];

    return {
      placeExists: row?.place_exists ?? false,
      total: row?.total ?? 0
    };
  }

  private async countReviews(userId: string) {
    const result = await measureReviewsDependency(
      "select",
      "place_reviews_count",
      async () =>
        this.db.query<{ total: number }>(
          `select count(*) as total
             from public.place_reviews r
             join public.places p
               on p.source = r.place_source
              and p.source_id = r.place_source_id
            where r.user_id = $1`,
          [userId]
        )
    );

    return result.rows[0]?.total ?? 0;
  }
}
