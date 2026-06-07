import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { SavedPlacesService } from "../../saved-places/index.js";
import type { MeService } from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

const meService: MeService = async (user) => ({
  user,
  profile: {
    userId: user.id,
    displayName: null,
    onboardingStatus: "not_started"
  }
});

function createSavedPlacesService(
  overrides: Partial<SavedPlacesService>
): SavedPlacesService {
  const unused = async () => {
    throw new Error("not used");
  };

  return {
    getSavedDashboard: unused,
    getCollectionDetail: unused,
    savePlace: unused,
    unsavePlace: unused,
    createCollection: unused,
    updateCollection: unused,
    deleteCollection: unused,
    addPlaceToCollection: unused,
    removePlaceFromCollection: unused,
    reorderCollectionPlaces: unused,
    getSavedPlaceIds: unused,
    listSavedPlaceIds: unused,
    getSavedPlaceStates: unused,
    ...overrides
  } as SavedPlacesService;
}

describe("me routes", () => {
  it("returns 401 when authorization is missing", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.me
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({
      status: "error",
      message: "Unauthorized"
    });
  });

  it("returns 401 when authorization is malformed", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.me,
      headers: {
        authorization: "valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({
      status: "error",
      message: "Unauthorized"
    });
  });

  it("returns 401 when the token is invalid", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.me,
      headers: {
        authorization: "Bearer invalid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({
      status: "error",
      message: "Unauthorized"
    });
  });

  it("returns the current user and profile when the token is valid", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.me,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      user: {
        id: authenticatedUser.id,
        email: authenticatedUser.email
      },
      profile: {
        userId: authenticatedUser.id,
        displayName: null,
        onboardingStatus: "not_started"
      }
    });
  });

  it("returns saved place ids for the authenticated user", async () => {
    const app = await buildApp({
      authService,
      meService,
      savedPlacesService: createSavedPlacesService({
        async listSavedPlaceIds(userId) {
          expect(userId).toBe(authenticatedUser.id);
          return [1, 7, 42];
        }
      })
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSavedIds,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      placeIds: [1, 7, 42]
    });
  });

  it("returns 401 for saved ids when authorization is missing", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.meSavedIds
    });

    await app.close();

    expect(response.statusCode).toBe(401);
  });

  it("does not expose unversioned me routes", async () => {
    const app = await buildApp({
      authService,
      meService
    });

    const response = await app.inject({
      method: "GET",
      url: AppRoute.Me,
      headers: {
        authorization: "Bearer valid-token"
      }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
