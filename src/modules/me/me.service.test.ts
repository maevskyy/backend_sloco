import { describe, expect, it } from "vitest";
import type { AuthenticatedUser } from "../auth/auth.service.js";
import { createMeService } from "./me.service.js";

const user: AuthenticatedUser = {
  id: "0f70a78a-05f8-45da-81b5-a435fdadf16c",
  email: "user@example.com"
};

describe("me service", () => {
  it("returns the authenticated user with an upserted profile", async () => {
    const meService = createMeService({
      async upsertDefaultProfile(userId) {
        return {
          userId,
          displayName: null,
          onboardingStatus: "not_started"
        };
      }
    });

    await expect(meService(user)).resolves.toEqual({
      user,
      profile: {
        userId: user.id,
        displayName: null,
        onboardingStatus: "not_started"
      }
    });
  });
});
