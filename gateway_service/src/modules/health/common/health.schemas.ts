import { z } from "zod";

export const healthStatusResponseSchema = z.object({
  status: z.literal("ok")
});

export const healthSchemaRegistry = z.registry<{ id: string }>();

healthSchemaRegistry.add(healthStatusResponseSchema, {
  id: "HealthStatusResponse"
});
