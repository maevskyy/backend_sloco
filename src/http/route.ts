import type { FastifySchema } from "fastify";

// Routes carry an OpenAPI schema for docs + response serialization, but request
// validation is done explicitly with zod in controllers. Disable Fastify's
// request validator so the OpenAPI request schema is docs-only.
export function docsRoute(schema: FastifySchema) {
  return {
    schema,
    validatorCompiler: () => () => true
  };
}
