import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { sharedErrorResponses } from "../../../config/http-schemas.js";
import { onboardingSchemaRegistry } from "./onboarding.schemas.js";

export const onboardingComponentSchemas = buildComponentSchemas(
  onboardingSchemaRegistry
);

const defineRoute = makeDefineRoute({
  tag: "Onboarding",
  errorResponses: sharedErrorResponses
});

export const onboardingCompleteRouteSchema = defineRoute({
  summary: "Finish onboarding.",
  description:
    "Saves the user's onboarding picks as saved places (they become favourite signals for the personalized feed) and records the onboarding status on the profile. Unknown place ids are skipped, not errors. Idempotent.",
  body: "OnboardingCompleteBody",
  ok: "OnboardingCompleteResponse"
});
