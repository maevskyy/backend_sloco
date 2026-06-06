import { z } from "zod";
import { buildComponentSchemas } from "./openapi.js";

// Shared HTTP error response schemas, referenced across modules by stable id.
// zod is the source of truth; OpenAPI components are generated from the registry.

export const errorResponseSchema = z.object({
  status: z.literal("error")
});

export const validationIssueSchema = z.looseObject({
  code: z.string().optional(),
  path: z.array(z.union([z.string(), z.number()])).optional(),
  message: z.string().optional()
});

export const validationErrorResponseSchema = z.object({
  status: z.literal("error"),
  message: z.string(),
  issues: z.array(validationIssueSchema)
});

export const authErrorResponseSchema = z.object({
  status: z.literal("error"),
  message: z.literal("Unauthorized")
});

export const notFoundResponseSchema = z.object({
  status: z.literal("error"),
  message: z.string()
});

export const httpSchemaRegistry = z.registry<{ id: string }>();

httpSchemaRegistry.add(errorResponseSchema, { id: "ErrorResponse" });
httpSchemaRegistry.add(validationErrorResponseSchema, {
  id: "ValidationErrorResponse"
});
httpSchemaRegistry.add(authErrorResponseSchema, { id: "AuthErrorResponse" });
httpSchemaRegistry.add(notFoundResponseSchema, { id: "NotFoundResponse" });

export const httpErrorComponentSchemas = buildComponentSchemas(httpSchemaRegistry);

// Shared error-response `$ref` set used by route schemas across modules.
export const sharedErrorResponses = {
  400: { $ref: "ValidationErrorResponse#" },
  401: { $ref: "AuthErrorResponse#" },
  404: { $ref: "NotFoundResponse#" },
  409: { $ref: "ErrorResponse#" },
  500: { $ref: "ErrorResponse#" }
} as const;
