import { z } from "zod";

// Single source of truth for the /me response shape.

export const meUserSchema = z.object({
  id: z.string().uuid(),
  email: z.string().nullable()
});

export const meProfileSchema = z.object({
  userId: z.string().uuid(),
  displayName: z.string().nullable(),
  // `not_started` is the row default; the only writer is
  // POST /v1/onboarding/complete (TASKS_38). The DB column stays free text;
  // this enum IS the API contract.
  onboardingStatus: z.enum(["not_started", "completed", "skipped"])
});

export const meResponseSchema = z.object({
  user: meUserSchema,
  profile: meProfileSchema
});

export const meSavedIdsResponseSchema = z.object({
  placeIds: z.array(z.number().int())
});

export const meSchemaRegistry = z.registry<{ id: string }>();

meSchemaRegistry.add(meUserSchema, { id: "MeUser" });
meSchemaRegistry.add(meProfileSchema, { id: "MeProfile" });
meSchemaRegistry.add(meResponseSchema, { id: "MeResponse" });
meSchemaRegistry.add(meSavedIdsResponseSchema, { id: "MeSavedIdsResponse" });
