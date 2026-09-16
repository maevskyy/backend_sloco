import { z } from "zod";

// Shared OpenAPI helpers. zod is the single source of truth for request/response
// shapes across modules; this turns a module's schema registry into Fastify
// component schemas and builds route schemas from a small declaration.

export type OpenApiComponentSchema = Record<string, unknown> & { $id: string };

/**
 * Generate Fastify component schemas from a zod registry.
 *
 * - `target: "openapi-3.0"` matches the 3.0.3 document emitted by @fastify/swagger
 *   (nullable as `nullable: true`, not `anyOf` with null).
 * - `unrepresentable: "any"` tolerates `.refine()` and other constructs that have
 *   no JSON Schema form.
 * - `$id` is normalized to the bare component id; cross-refs stay `Name#`.
 *
 * Gotcha: use `.min(1)` (not `.positive()`) for positive integers — in the
 * openapi-3.0 target `.positive()` emits a boolean `exclusiveMinimum` that
 * Fastify's serializer rejects.
 */
export function buildComponentSchemas(
  registry: z.core.$ZodRegistry<{ id: string }>
): OpenApiComponentSchema[] {
  const generated = z.toJSONSchema(registry, {
    uri: (id) => `${id}#`,
    unrepresentable: "any",
    target: "openapi-3.0"
  });

  return Object.entries(generated.schemas).map(([id, schema]) => ({
    ...(schema as Record<string, unknown>),
    $id: id
  }));
}

type RouteResponses = Record<number, { $ref: string }>;

type RouteDefinition = {
  summary: string;
  description?: string;
  params?: string;
  body?: string;
  query?: string;
  ok: string;
  /** Success status code for `ok` (default 200; e.g. 202 for async intake). */
  okStatus?: number;
};

/**
 * Build a module-specific `defineRoute` helper that injects the shared `tags`,
 * bearer `security`, and error responses, so each route schema stays one line.
 * Component ids are referenced as `Name#`.
 */
export function makeDefineRoute(config: {
  tag: string;
  security?: boolean;
  errorResponses: RouteResponses;
}) {
  const security = config.security === false ? undefined : [{ bearerAuth: [] }];

  return function defineRoute(route: RouteDefinition) {
    return {
      tags: [config.tag],
      summary: route.summary,
      ...(route.description ? { description: route.description } : {}),
      ...(security ? { security } : {}),
      ...(route.params ? { params: { $ref: `${route.params}#` } } : {}),
      ...(route.query ? { querystring: { $ref: `${route.query}#` } } : {}),
      ...(route.body ? { body: { $ref: `${route.body}#` } } : {}),
      response: {
        [route.okStatus ?? 200]: { $ref: `${route.ok}#` },
        ...config.errorResponses
      }
    } as const;
  };
}
