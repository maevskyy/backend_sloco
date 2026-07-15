import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import {
  PlaceNotFoundError,
  type PlaceReaction,
  type ReactionsResult,
  type ReactionsService
} from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

function createReactionsService(
  overrides: Partial<ReactionsService> = {}
): ReactionsService {
  const reactions = new Map<number, PlaceReaction>();

  const list = (): ReactionsResult => ({
    favorites: [...reactions.entries()]
      .filter(([, reaction]) => reaction === "favorite")
      .map(([placeId]) => placeId)
      .sort((left, right) => left - right),
    dislikes: [...reactions.entries()]
      .filter(([, reaction]) => reaction === "dislike")
      .map(([placeId]) => placeId)
      .sort((left, right) => left - right),
    hidden: [...reactions.entries()]
      .filter(([, reaction]) => reaction === "hide")
      .map(([placeId]) => placeId)
      .sort((left, right) => left - right)
  });

  return {
    async setReaction(_userId, placeId, reaction) {
      reactions.set(placeId, reaction);
      return { placeId, reaction };
    },
    async deleteReaction(_userId, placeId) {
      reactions.delete(placeId);
    },
    async getReactions() {
      return list();
    },
    async getReactionMap(_userId, placeIds) {
      return new Map(
        placeIds
          .filter((placeId) => reactions.has(placeId))
          .map((placeId) => [placeId, reactions.get(placeId)!])
      );
    },
    ...overrides
  };
}

describe("reactions routes", () => {
  it("returns 401 when listing reactions without auth", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meReactions
    });

    await app.close();

    expect(response.statusCode).toBe(401);
  });

  it("returns empty grouped reactions when the user has none", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService()
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meReactions,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      favorites: [],
      dislikes: [],
      hidden: []
    });
  });

  it("round-trips a reaction through PUT then GET", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService()
    });

    const putResponse = await app.inject({
      method: "PUT",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        reaction: "favorite"
      }
    });

    const getResponse = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meReactions,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(putResponse.statusCode).toBe(200);
    expect(putResponse.json()).toEqual({
      placeId: 123,
      reaction: "favorite"
    });
    expect(getResponse.json()).toEqual({
      favorites: [123],
      dislikes: [],
      hidden: []
    });
  });

  it("replaces a reaction when a second PUT sets another value", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService()
    });

    await app.inject({
      method: "PUT",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        reaction: "favorite"
      }
    });

    await app.inject({
      method: "PUT",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        reaction: "hide"
      }
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meReactions,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      favorites: [],
      dislikes: [],
      hidden: [123]
    });
  });

  it("deletes a reaction with 204 and removes it from grouped results", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService()
    });

    await app.inject({
      method: "PUT",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        reaction: "dislike"
      }
    });

    const deleteResponse = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "123"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    const getResponse = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meReactions,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(deleteResponse.statusCode).toBe(204);
    expect(getResponse.json()).toEqual({
      favorites: [],
      dislikes: [],
      hidden: []
    });
  });

  it("returns 404 when setting a reaction for an unknown place", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService({
        async setReaction(_userId, placeId) {
          throw new PlaceNotFoundError(placeId);
        }
      })
    });

    const response = await app.inject({
      method: "PUT",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "999"),
      headers: {
        authorization: "Bearer valid-token"
      },
      payload: {
        reaction: "favorite"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({
      status: "error",
      message: "Place not found"
    });
  });

  it("returns 404 when deleting a reaction for an unknown place", async () => {
    const app = await buildApp({
      authService,
      reactionsService: createReactionsService({
        async deleteReaction(_userId, placeId) {
          throw new PlaceNotFoundError(placeId);
        }
      })
    });

    const response = await app.inject({
      method: "DELETE",
      url: VersionedAppRoute.mePlaceReaction.replace(":placeId", "999"),
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
    expect(response.json()).toEqual({
      status: "error",
      message: "Place not found"
    });
  });
});
