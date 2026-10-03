import { describe, expect, it } from "vitest";
import type { Db, DbExecutor, DbQueryResult } from "../../../lib/db.js";
import { SavedPlacesStore } from "../index.js";

const userId = "0f70a78a-05f8-45da-81b5-a435fdadf16c";
const savedAt = "2026-10-03T04:25:16.123456+00:00";
const keepId = "4b572b66-d74d-49bb-b9b5-9780c266c6f7";
const dropId = "9d1c3f0e-7a52-4c1b-8f0e-2b6a1d9e4c10";

type Statement = { text: string; params: readonly unknown[] };

// A Db that behaves like a real transaction from the outside: statements run
// inside `transaction` only reach `committed` when the callback resolves; a
// throw discards them (ROLLBACK). Statements on the outer Db commit at once.
function createFakeDb(options: {
  failOn?: RegExp;
  respond?: (text: string) => Record<string, unknown>[];
}) {
  const committed: Statement[] = [];
  const attempted: Statement[] = [];
  let transactions = 0;

  const run = async <T>(
    sink: Statement[],
    text: string,
    params: readonly unknown[] = []
  ): Promise<DbQueryResult<T>> => {
    const statement = { text, params };
    attempted.push(statement);

    if (options.failOn?.test(text)) {
      throw Object.assign(new Error("simulated failure"), { code: "40001" });
    }

    sink.push(statement);
    const rows = (options.respond?.(text) ?? []) as T[];
    return { rows, rowCount: rows.length };
  };

  const db: Db = {
    query: (text, params) => run(committed, text, params),
    async transaction(fn) {
      transactions += 1;
      const pending: Statement[] = [];
      const tx: DbExecutor = {
        query: (text, params) => run(pending, text, params)
      };
      const result = await fn(tx);
      committed.push(...pending);
      return result;
    }
  };

  return {
    db,
    committed,
    attempted,
    get transactions() {
      return transactions;
    }
  };
}

const isWrite = (statement: Statement) =>
  /^\s*(insert|update|delete)\b/i.test(statement.text);

describe("saved places store transactions", () => {
  it("commits save + add + remove together", async () => {
    const fake = createFakeDb({
      respond: (text) =>
        /insert into public\.saved_places/.test(text) ? [{ created_at: savedAt }] : []
    });
    const store = new SavedPlacesStore(fake.db);

    await expect(
      store.savePlaceWithCollections(userId, 123, { add: [keepId], remove: [dropId] })
    ).resolves.toBe(savedAt);

    expect(fake.transactions).toBe(1);
    expect(fake.committed.map((statement) => statement.text)).toEqual([
      expect.stringMatching(/insert into public\.saved_places/),
      expect.stringMatching(/insert into public\.saved_collection_places/),
      expect.stringMatching(/delete from public\.saved_collection_places/)
    ]);
  });

  it("rolls back the save and the add when the remove fails", async () => {
    const fake = createFakeDb({
      failOn: /delete from public\.saved_collection_places/,
      respond: (text) =>
        /insert into public\.saved_places/.test(text) ? [{ created_at: savedAt }] : []
    });
    const store = new SavedPlacesStore(fake.db);

    await expect(
      store.savePlaceWithCollections(userId, 123, { add: [keepId], remove: [dropId] })
    ).rejects.toThrow("simulated failure");

    // Both earlier writes were issued — inside the transaction — and none survived.
    expect(fake.attempted.filter(isWrite)).toHaveLength(3);
    expect(fake.committed).toEqual([]);
  });

  it("keeps memberships when deleting the bookmark fails on unsave", async () => {
    const fake = createFakeDb({ failOn: /delete from public\.saved_places/ });
    const store = new SavedPlacesStore(fake.db);

    await expect(store.unsavePlace(userId, 123)).rejects.toThrow("simulated failure");

    expect(fake.attempted.filter(isWrite)).toHaveLength(2);
    expect(fake.committed).toEqual([]);
  });

  it("creates no system list when a later one fails", async () => {
    let inserted = 0;
    const fake = createFakeDb({
      respond: (text) => {
        if (!/insert into public\.saved_collections/.test(text)) return [];
        inserted += 1;
        if (inserted === 3) throw new Error("simulated failure");
        return [{ id: `system-${inserted}`, slug: null, is_default: false }];
      }
    });
    const store = new SavedPlacesStore(fake.db);

    await expect(store.ensureSystemCollections(userId)).rejects.toThrow(
      "simulated failure"
    );

    // Only the initial read (outside the transaction) is left; the two lists
    // inserted before the failure were rolled back with it.
    expect(fake.committed.filter(isWrite)).toEqual([]);
    expect(fake.committed.map((statement) => statement.text)).toEqual([
      expect.stringMatching(/from public\.saved_collections/)
    ]);
  });
});
