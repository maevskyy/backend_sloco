import { describe, expect, it } from "vitest";
import {
  createReactionsService,
  PlaceNotFoundError,
  type ReactionsStoreContract
} from "../index.js";

const userId = "0f70a78a-05f8-45da-81b5-a435fdadf16c";

function createStore(
  overrides: Partial<ReactionsStoreContract> = {}
): ReactionsStoreContract {
  return {
    async setReaction() {},
    async deleteReaction() {},
    async listReactions() {
      return {
        favorites: [123],
        dislikes: [456],
        hidden: [789]
      };
    },
    async getReactions() {
      return new Map([[123, "favorite" as const]]);
    },
    ...overrides
  };
}

describe("reactions service", () => {
  it("sets a reaction and returns the public DTO", async () => {
    let captured:
      | { userId: string; placeId: number; reaction: string }
      | undefined;
    const service = createReactionsService(
      createStore({
        async setReaction(currentUserId, placeId, reaction) {
          captured = { userId: currentUserId, placeId, reaction };
        }
      })
    );

    await expect(
      service.setReaction(userId, 123, "favorite")
    ).resolves.toEqual({
      placeId: 123,
      reaction: "favorite"
    });

    expect(captured).toEqual({
      userId,
      placeId: 123,
      reaction: "favorite"
    });
  });

  it("deletes a reaction idempotently", async () => {
    let captured:
      | { userId: string; placeId: number }
      | undefined;
    const service = createReactionsService(
      createStore({
        async deleteReaction(currentUserId, placeId) {
          captured = { userId: currentUserId, placeId };
        }
      })
    );

    await expect(service.deleteReaction(userId, 123)).resolves.toBeUndefined();
    expect(captured).toEqual({ userId, placeId: 123 });
  });

  it("returns grouped reactions", async () => {
    const service = createReactionsService(createStore());

    await expect(service.getReactions(userId)).resolves.toEqual({
      favorites: [123],
      dislikes: [456],
      hidden: [789]
    });
  });

  it("returns a reaction map for downstream callers", async () => {
    const service = createReactionsService(createStore());

    await expect(service.getReactionMap(userId, [123])).resolves.toEqual(
      new Map([[123, "favorite"]])
    );
  });

  it("surfaces place-not-found from the store", async () => {
    const service = createReactionsService(
      createStore({
        async setReaction(_userId, placeId) {
          throw new PlaceNotFoundError(placeId);
        }
      })
    );

    await expect(
      service.setReaction(userId, 999, "hide")
    ).rejects.toBeInstanceOf(PlaceNotFoundError);
  });
});
