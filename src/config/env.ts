import "dotenv/config";
import { z } from "zod";

const optionalNonEmptyString = (schema: z.ZodString) =>
  z.preprocess((value) => (value === "" ? undefined : value), schema.optional());

const envSchema = z.object({
  NODE_ENV: z
    .enum(["development", "test", "production"])
    .default("development"),
  HOST: z.string().default("0.0.0.0"),
  PORT: z.coerce.number().int().positive().default(3000),
  SUPABASE_URL: optionalNonEmptyString(z.string().url()),
  SUPABASE_SERVICE_ROLE_KEY: optionalNonEmptyString(z.string().min(1)),
  RECOMMENDATION_SERVICE_URL: optionalNonEmptyString(z.string().url())
});

export const env = envSchema.parse(process.env);
