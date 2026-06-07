import { z } from "zod";

// Single source of truth for the /me response shape.

export const meUserSchema = z.object({
  id: z.string().uuid(),
  email: z.string().nullable()
});

export const meProfileSchema = z.object({
  userId: z.string().uuid(),
  displayName: z.string().nullable(),
  onboardingStatus: z.string()
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
