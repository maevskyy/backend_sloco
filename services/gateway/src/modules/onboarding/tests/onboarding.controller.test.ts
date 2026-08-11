import { describe, expect, it } from "vitest";
import { buildApp } from "../../../app.js";
import { AppRoute, VersionedAppRoute } from "../../../config/routes.js";
import type { AuthService, AuthenticatedUser } from "../../auth/auth.service.js";
import type { OnboardingService } from "../index.js";

const authenticatedUser: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

const authService: AuthService = {
  async getUserFromToken(token) {
    return token === "valid-token" ? authenticatedUser : null;
  }
};

describe("onboarding routes", () => {
  it("returns 401 without a token", async () => {
    const app = await buildApp({
      authService,
      onboardingService: {
        async completeOnboarding() {
          throw new Error("must not be called");
        }
      }
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.onboardingComplete,
      payload: { pickedPlaceIds: [1], status: "completed" }
    });

    await app.close();

    expect(response.statusCode).toBe(401);
    expect(response.json()).toEqual({
      status: "error",
      message: "Unauthorized"
    });
  });

  it("completes onboarding for an authenticated user", async () => {
    const service: OnboardingService = {
      async completeOnboarding(userId, input) {
        expect(userId).toBe(authenticatedUser.id);
        expect(input).toEqual({
          pickedPlaceIds: [11, 22, 33],
          status: "completed"
        });

        return { onboardingStatus: "completed", savedCount: 3 };
      }
    };
    const app = await buildApp({
      authService,
      onboardingService: service
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.onboardingComplete,
      headers: { authorization: "Bearer valid-token" },
      payload: { pickedPlaceIds: [11, 22, 33], status: "completed" }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json()).toEqual({
      onboardingStatus: "completed",
      savedCount: 3
    });
  });

  it("accepts an empty skip", async () => {
    const app = await buildApp({
      authService,
      onboardingService: {
        async completeOnboarding(_userId, input) {
          expect(input).toEqual({ pickedPlaceIds: [], status: "skipped" });
          return { onboardingStatus: "skipped", savedCount: 0 };
        }
      }
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.onboardingComplete,
      headers: { authorization: "Bearer valid-token" },
      payload: { pickedPlaceIds: [], status: "skipped" }
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().onboardingStatus).toBe("skipped");
  });

  it("returns 400 for an unknown status value", async () => {
    const app = await buildApp({
      authService,
      onboardingService: {
        async completeOnboarding() {
          throw new Error("must not be called");
        }
      }
    });

    const response = await app.inject({
      method: "POST",
      url: VersionedAppRoute.onboardingComplete,
      headers: { authorization: "Bearer valid-token" },
      payload: { pickedPlaceIds: [1], status: "done" }
    });

    await app.close();

    expect(response.statusCode).toBe(400);
  });

  it("exposes the endpoint in the OpenAPI document", async () => {
    const app = await buildApp({
      authService,
      onboardingService: {
        async completeOnboarding() {
          return { onboardingStatus: "completed", savedCount: 0 };
        }
      }
    });

    const response = await app.inject({
      method: "GET",
      url: VersionedAppRoute.swaggerOpenApiJson
    });

    await app.close();

    expect(response.statusCode).toBe(200);
    expect(response.json().paths).toHaveProperty(
      VersionedAppRoute.onboardingComplete
    );
  });

  it("does not expose unversioned onboarding routes", async () => {
    const app = await buildApp();

    const response = await app.inject({
      method: "POST",
      url: AppRoute.OnboardingComplete,
      payload: { pickedPlaceIds: [], status: "skipped" }
    });

    await app.close();

    expect(response.statusCode).toBe(404);
  });
});
