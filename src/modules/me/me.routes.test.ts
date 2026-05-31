import { describe, expect, it } from "vitest";
import { buildApp } from "../../app.js";
import { AppRoute, VersionedAppRoute } from "../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../auth/auth.service.js";
import type { MeService } from "./me.service.js";

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
