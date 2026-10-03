import { getDb, type Db, type DbExecutor } from "../../../lib/db.js";
import { measureDependencyMetric } from "../../../observability/metrics.js";
import {
  mapCollectionPlaceRow,
  mapSavedPlaceRow
} from "../common/saved-places.mappers.js";
import type {
  SavedCollectionPlaceRow,
  SavedCollectionRow,
  SavedPlaceRow,
  SavedPlaceState,
  SavedPlacesStoreContract,
  SavedPlaceSummary
} from "../common/saved-places.types.js";

// Direct Postgres (SLO-49), same pool as every other store. RLS is enabled on
// saved_places / saved_collections / saved_collection_places with no policies;
// like events_raw (TASKS_51) the direct pool's role is not subject to it, so
// ownership is enforced by the user_id filter in every statement below.
// Multi-statement writes run in ONE transaction: a failure part-way leaves the
// user's bookmarks and lists exactly as they were.

// The three SYSTEM lists every user gets (TASKS_54). They are auto-created, cannot
// be deleted, are hidden from "My lists" by the client and are pinned to the top of
// its save picker. `slug` is their stable identity; `name` is display text.
// `saved` is also the default: a save that names no list lands there.
const SYSTEM_COLLECTIONS = [
  { slug: "saved", name: "Saved", colorHex: "#f0805f", sortOrder: 0, isDefault: true },
  { slug: "favorites", name: "Favorites", colorHex: "#e6b15c", sortOrder: 1, isDefault: false },
  { slug: "been", name: "Been there", colorHex: "#8fb996", sortOrder: 2, isDefault: false }
] as const;

export const SYSTEM_COLLECTION_SLUGS = SYSTEM_COLLECTIONS.map((item) => item.slug);

// The former PostgREST embed `places!inner(...)`: an inner JOIN whose place
// columns are folded into one `places` object, built by Postgres' own JSON
// encoder exactly like PostgREST did (numbers stay numbers, attributes stays
// the jsonb object), so the mappers see the shape they always saw.
const PLACE_JSON = `json_build_object(
  'id', p.id,
  'source', p.source,
  'source_id', p.source_id,
  'name', p.name,
  'country', p.country,
  'city', p.city,
  'category', p.category,
  'latitude', p.latitude,
  'longitude', p.longitude,
  'rating', p.rating,
  'price_level', p.price_level,
  'attributes', p.attributes
)`;

function measureSavedPlacesDependency<T>(
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

// PostgREST `.single()` semantics: anything but exactly one row is an error.
function singleRow<T>(rows: T[], what: string): T {
  if (rows.length !== 1) {
    throw new Error(`${what}: expected exactly one row, got ${rows.length}`);
  }

  return rows[0] as T;
}

// listCollectionPlaces calls for one user in one tick, pending a single query.
type CollectionPlacesBatch = {
  collectionIds: Set<string>;
  placesById: Promise<Map<string, SavedPlaceSummary[]>>;
};

export class SavedPlacesStore implements SavedPlacesStoreContract {
  private readonly collectionPlacesBatches = new Map<string, CollectionPlacesBatch>();

  constructor(private readonly db: Db = getDb()) {}

  async placeExists(placeId: number) {
    const result = await measureSavedPlacesDependency(
      "select",
      "places_exists",
      async () =>
        this.db.query("select 1 from public.places where id = $1 limit 1", [
          placeId
        ])
    );

    return result.rows.length > 0;
  }

  /**
   * Create the three system lists for this user if they are missing, and return
   * them by slug. Idempotent, and safe against a user who already created a list
   * under a system NAME: that row is adopted (its slug is filled in) instead of
   * colliding with `unique (user_id, name)`.
   *
   * The common case (all three exist) is one read and no transaction. Missing
   * lists are written in ONE transaction; a concurrent request creating the same
   * lists is absorbed by `on conflict do nothing` + a read-back (a caught 23505
   * would abort the transaction).
   */
  async ensureSystemCollections(userId: string) {
    const existing = await selectCollections(this.db, userId);
    const bySlug = new Map<string, SavedCollectionRow>(
      existing
        .filter((row) => row.slug !== null)
        .map((row) => [row.slug as string, row])
    );

    if (SYSTEM_COLLECTIONS.every((system) => bySlug.has(system.slug))) {
      return bySlug;
    }

    return this.db.transaction(async (tx) => {
      for (const system of SYSTEM_COLLECTIONS) {
        if (bySlug.has(system.slug)) continue;

        const sameName = existing.find(
          (row) => row.slug === null && row.name === system.name
        );

        if (sameName) {
          const adopted = await measureSavedPlacesDependency(
            "update",
            "saved_collections_adopt_system",
            async () =>
              tx.query<SavedCollectionRow>(
                `update public.saved_collections
                    set slug = $3
                  where id = $1
                    and user_id = $2
                  returning *`,
                [sameName.id, userId, system.slug]
              )
          );

          bySlug.set(
            system.slug,
            singleRow(adopted.rows, "saved_collections_adopt_system")
          );
          continue;
        }

        const inserted = await measureSavedPlacesDependency(
          "insert",
          "saved_collections_system",
          async () =>
            tx.query<SavedCollectionRow>(
              `insert into public.saved_collections (
                 user_id, name, slug, color_hex, is_default, sort_order
               )
               values ($1, $2, $3, $4, $5, $6)
               on conflict do nothing
               returning *`,
              [
                userId,
                system.name,
                system.slug,
                system.colorHex,
                // The default flag has a partial unique index (one per user), so it
                // is only claimed when the user has no default yet.
                system.isDefault && !existing.some((row) => row.is_default),
                system.sortOrder
              ]
            )
        );

        const created = inserted.rows[0];

        if (created) {
          bySlug.set(system.slug, created);
          continue;
        }

        // Lost a race with a concurrent request: read back what the winner wrote.
        const raced = await measureSavedPlacesDependency(
          "select",
          "saved_collections_system_refetch",
          async () =>
            tx.query<SavedCollectionRow>(
              `select *
                 from public.saved_collections
                where user_id = $1
                  and slug = $2`,
              [userId, system.slug]
            )
        );

        bySlug.set(
          system.slug,
          singleRow(raced.rows, "saved_collections_system_refetch")
        );
      }

      return bySlug;
    });
  }

  async ensureDefaultCollection(userId: string) {
    const bySlug = await this.ensureSystemCollections(userId);
    const fallback = bySlug.get("saved");

    if (fallback) return fallback;

    // Only reachable if someone cleared the slug by hand.
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collections_default",
      async () =>
        this.db.query<SavedCollectionRow>(
          `select *
             from public.saved_collections
            where user_id = $1
              and is_default = true`,
          [userId]
        )
    );

    return singleRow(result.rows, "saved_collections_default");
  }

  /** Membership of one place across the user's lists. */
  async listPlaceCollectionIds(userId: string, placeId: number) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_of_place",
      async () =>
        this.db.query<{ collection_id: string }>(
          `select collection_id
             from public.saved_collection_places
            where user_id = $1
              and place_id = $2`,
          [userId, placeId]
        )
    );

    return result.rows.map((row) => row.collection_id);
  }

  async removePlaceFromCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    await deletePlaceFromCollections(this.db, userId, placeId, collectionIds);
  }

  async listCollections(userId: string) {
    return selectCollections(this.db, userId);
  }

  async getCollectionsByIds(userId: string, collectionIds: string[]) {
    if (collectionIds.length === 0) return [];

    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collections_by_ids",
      async () =>
        this.db.query<SavedCollectionRow>(
          `select *
             from public.saved_collections
            where user_id = $1
              and id = any($2::uuid[])`,
          [userId, collectionIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows;
  }

  async getCollection(userId: string, collectionId: string) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collections_get",
      async () =>
        this.db.query<SavedCollectionRow>(
          `select *
             from public.saved_collections
            where user_id = $1
              and id = $2`,
          [userId, collectionId]
        )
    );

    return result.rows[0] ?? null;
  }

  async createCollection(
    userId: string,
    input: { name: string; colorHex?: string }
  ) {
    const result = await measureSavedPlacesDependency(
      "insert",
      "saved_collections_create",
      async () =>
        this.db.query<SavedCollectionRow>(
          `insert into public.saved_collections (user_id, name, color_hex, is_default)
           values ($1, $2, $3, false)
           returning *`,
          [userId, input.name, input.colorHex ?? null]
        )
    );

    return singleRow(result.rows, "saved_collections_create");
  }

  async updateCollection(
    userId: string,
    collectionId: string,
    input: { name?: string; colorHex?: string | null; sortOrder?: number }
  ) {
    const { assignments, values } = buildCollectionUpdate(input);
    const result = await measureSavedPlacesDependency(
      "update",
      "saved_collections_update",
      async () =>
        this.db.query<SavedCollectionRow>(
          `update public.saved_collections
              set ${assignments}
            where user_id = $1
              and id = $2
            returning *`,
          [userId, collectionId, ...values]
        )
    );

    return result.rows[0] ?? null;
  }

  // Memberships go with the list: saved_collection_places.collection_id is
  // `on delete cascade`, so this one statement is atomic on its own.
  async deleteCollection(userId: string, collectionId: string) {
    await measureSavedPlacesDependency(
      "delete",
      "saved_collections_delete",
      async () =>
        this.db.query(
          `delete from public.saved_collections
            where user_id = $1
              and id = $2`,
          [userId, collectionId]
        )
    );
  }

  async savePlace(userId: string, placeId: number) {
    return upsertSavedPlace(this.db, userId, placeId);
  }

  /** Drops the place from every list and from saved_places, in one transaction. */
  async unsavePlace(userId: string, placeId: number) {
    await this.db.transaction(async (tx) => {
      await measureSavedPlacesDependency(
        "delete",
        "saved_collection_places_unsave_memberships",
        async () =>
          tx.query(
            `delete from public.saved_collection_places
              where user_id = $1
                and place_id = $2`,
            [userId, placeId]
          )
      );

      await measureSavedPlacesDependency(
        "delete",
        "saved_places_unsave",
        async () =>
          tx.query(
            `delete from public.saved_places
              where user_id = $1
                and place_id = $2`,
            [userId, placeId]
          )
      );
    });
  }

  async savePlaceWithCollections(
    userId: string,
    placeId: number,
    change: { add: string[]; remove: string[] }
  ) {
    return this.db.transaction(async (tx) => {
      const savedAt = await upsertSavedPlace(tx, userId, placeId);
      await insertPlaceIntoCollections(tx, userId, placeId, change.add);
      await deletePlaceFromCollections(tx, userId, placeId, change.remove);

      return savedAt;
    });
  }

  async addPlaceToCollections(
    userId: string,
    placeId: number,
    collectionIds: string[]
  ) {
    await insertPlaceIntoCollections(this.db, userId, placeId, collectionIds);
  }

  async removePlaceFromCollection(
    userId: string,
    collectionId: string,
    placeId: number
  ) {
    await measureSavedPlacesDependency(
      "delete",
      "saved_collection_places_remove",
      async () =>
        this.db.query(
          `delete from public.saved_collection_places
            where user_id = $1
              and collection_id = $2
              and place_id = $3`,
          [userId, collectionId, placeId]
        )
    );
  }

  async listSavedPlaces(userId: string, limit: number) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_places_list",
      async () =>
        this.db.query<SavedPlaceRow>(
          `select sp.created_at, sp.last_viewed_at, ${PLACE_JSON} as places
             from public.saved_places sp
             join public.places p on p.id = sp.place_id
            where sp.user_id = $1
            order by sp.created_at desc
            limit $2`,
          [userId, limit]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows.map(mapSavedPlaceRow);
  }

  async countSavedPlaces(userId: string) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_places_count",
      async () =>
        this.db.query<{ count: number }>(
          `select count(*) as count
             from public.saved_places
            where user_id = $1`,
          [userId]
        ),
      (queryResult) => queryResult.rows[0]?.count
    );

    return result.rows[0]?.count ?? 0;
  }

  async listSavedPlaceIds(userId: string) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_places_ids",
      async () =>
        this.db.query<{ place_id: number }>(
          `select place_id
             from public.saved_places
            where user_id = $1
            order by created_at desc`,
          [userId]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return result.rows.map((row) => row.place_id);
  }

  // The dashboard and the collection page ask for every list's places at once
  // (service buildCollections, Promise.all over the user's lists — no cap on
  // their number). One statement per list would hold one pool client per list.
  // Calls for the same user made in the same tick are folded into ONE
  // statement (`collection_id = any($2)`) and split per list here; each caller
  // gets exactly the rows, in the order, it would have got alone.
  listCollectionPlaces(
    userId: string,
    collectionId: string
  ): Promise<SavedPlaceSummary[]> {
    let batch = this.collectionPlacesBatches.get(userId);

    if (!batch) {
      const collectionIds = new Set<string>();
      // Runs as a microtask: after every synchronous caller of this tick has
      // added its id, before any I/O.
      const placesById = Promise.resolve().then(() => {
        this.collectionPlacesBatches.delete(userId);
        return this.fetchCollectionPlaces(userId, [...collectionIds]);
      });
      batch = { collectionIds, placesById };
      this.collectionPlacesBatches.set(userId, batch);
    }

    batch.collectionIds.add(collectionId);

    return batch.placesById.then((placesById) => [
      ...(placesById.get(collectionId) ?? [])
    ]);
  }

  private async fetchCollectionPlaces(userId: string, collectionIds: string[]) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_list",
      async () =>
        this.db.query<SavedCollectionPlaceRow>(
          `select scp.collection_id,
                  scp.place_id,
                  scp.sort_order,
                  scp.created_at,
                  ${PLACE_JSON} as places
             from public.saved_collection_places scp
             join public.places p on p.id = scp.place_id
            where scp.user_id = $1
              and scp.collection_id = any($2::uuid[])
            order by scp.sort_order asc, scp.created_at asc`,
          [userId, collectionIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    const placesById = new Map<string, SavedPlaceSummary[]>();

    for (const row of result.rows) {
      const places = placesById.get(row.collection_id) ?? [];
      places.push(mapCollectionPlaceRow(row));
      placesById.set(row.collection_id, places);
    }

    return placesById;
  }

  // One set-based UPDATE (atomic by itself) instead of one call per place:
  // placeIds[i] gets sort_order i, as before.
  async reorderCollectionPlaces(
    userId: string,
    collectionId: string,
    placeIds: number[]
  ) {
    if (placeIds.length === 0) return;

    await measureSavedPlacesDependency(
      "update",
      "saved_collection_places_reorder",
      async () =>
        this.db.query(
          `update public.saved_collection_places as scp
              set sort_order = (ordered.position - 1)::integer
             from unnest($3::bigint[]) with ordinality as ordered(place_id, position)
            where scp.user_id = $1
              and scp.collection_id = $2
              and scp.place_id = ordered.place_id`,
          [userId, collectionId, placeIds]
        ),
      () => placeIds.length
    );
  }

  async getSavedPlaceStates(userId: string, placeIds: number[]) {
    if (placeIds.length === 0) return new Map<number, SavedPlaceState>();

    const states = await this.getSavedPlaceRows(userId, placeIds);
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_collection_places_states",
      async () =>
        this.db.query<SavedMembershipRow>(
          `select place_id, collection_id
             from public.saved_collection_places
            where user_id = $1
              and place_id = any($2::bigint[])`,
          [userId, placeIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    for (const row of result.rows) {
      const existing =
        states.get(row.place_id) ?? ({ isSaved: false, collectionIds: [] });

      existing.collectionIds.push(row.collection_id);
      states.set(row.place_id, existing);
    }

    return states;
  }

  private async getSavedPlaceRows(userId: string, placeIds: number[]) {
    const result = await measureSavedPlacesDependency(
      "select",
      "saved_places_states",
      async () =>
        this.db.query<{ place_id: number }>(
          `select place_id
             from public.saved_places
            where user_id = $1
              and place_id = any($2::bigint[])`,
          [userId, placeIds]
        ),
      (queryResult) => queryResult.rowCount ?? undefined
    );

    return new Map<number, SavedPlaceState>(
      result.rows.map((row) => [row.place_id, { isSaved: true, collectionIds: [] }])
    );
  }
}

type SavedMembershipRow = {
  place_id: number;
  collection_id: string;
};

// Statement helpers shared by the single-call methods (outer Db) and the
// transactional ones (tx) — same SQL, same metric names either way.

async function selectCollections(executor: DbExecutor, userId: string) {
  const result = await measureSavedPlacesDependency(
    "select",
    "saved_collections_list",
    async () =>
      executor.query<SavedCollectionRow>(
        `select *
           from public.saved_collections
          where user_id = $1
          order by sort_order asc, created_at asc`,
        [userId]
      ),
    (queryResult) => queryResult.rowCount ?? undefined
  );

  return result.rows;
}

// Insert-or-keep (the old upsert with ignoreDuplicates): a re-save keeps the
// original created_at, which is then read back.
async function upsertSavedPlace(
  executor: DbExecutor,
  userId: string,
  placeId: number
) {
  const inserted = await measureSavedPlacesDependency(
    "upsert",
    "saved_places_save",
    async () =>
      executor.query<{ created_at: string }>(
        `insert into public.saved_places (user_id, place_id)
         values ($1, $2)
         on conflict (user_id, place_id) do nothing
         returning created_at`,
        [userId, placeId]
      )
  );

  const createdAt = inserted.rows[0]?.created_at;
  if (createdAt) return createdAt;

  const existing = await measureSavedPlacesDependency(
    "select",
    "saved_places_created_at",
    async () =>
      executor.query<{ created_at: string }>(
        `select created_at
           from public.saved_places
          where user_id = $1
            and place_id = $2`,
        [userId, placeId]
      )
  );

  return singleRow(existing.rows, "saved_places_created_at").created_at;
}

async function insertPlaceIntoCollections(
  executor: DbExecutor,
  userId: string,
  placeId: number,
  collectionIds: string[]
) {
  if (collectionIds.length === 0) return;

  await measureSavedPlacesDependency(
    "upsert",
    "saved_collection_places_add",
    async () =>
      executor.query(
        `insert into public.saved_collection_places (collection_id, user_id, place_id)
         select unnest($1::uuid[]), $2::uuid, $3::bigint
         on conflict (collection_id, place_id) do nothing`,
        [collectionIds, userId, placeId]
      ),
    () => collectionIds.length
  );
}

async function deletePlaceFromCollections(
  executor: DbExecutor,
  userId: string,
  placeId: number,
  collectionIds: string[]
) {
  if (collectionIds.length === 0) return;

  await measureSavedPlacesDependency(
    "delete",
    "saved_collection_places_remove_many",
    async () =>
      executor.query(
        `delete from public.saved_collection_places
          where user_id = $1
            and place_id = $2
            and collection_id = any($3::uuid[])`,
        [userId, placeId, collectionIds]
      ),
    () => collectionIds.length
  );
}

// SET list from fixed column names only; values are parameters from $3 on
// ($1/$2 are user_id and id in the WHERE clause).
function buildCollectionUpdate(input: {
  name?: string;
  colorHex?: string | null;
  sortOrder?: number;
}) {
  const columns: string[] = ["updated_at"];
  const values: unknown[] = [new Date().toISOString()];

  if (input.name !== undefined) {
    columns.push("name");
    values.push(input.name);
  }
  if (input.colorHex !== undefined) {
    columns.push("color_hex");
    values.push(input.colorHex);
  }
  if (input.sortOrder !== undefined) {
    columns.push("sort_order");
    values.push(input.sortOrder);
  }

  return {
    assignments: columns
      .map((column, index) => `${column} = $${index + 3}`)
      .join(", "),
    values
  };
}
