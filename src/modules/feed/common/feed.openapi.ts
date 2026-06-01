import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { feedSchemaRegistry } from "./feed.schemas.js";

export const feedComponentSchemas = buildComponentSchemas(feedSchemaRegistry);

const defineRoute = makeDefineRoute({
  tag: "Feed",
  security: false,
  errorResponses: {
    400: { $ref: "ValidationErrorResponse#" },
    401: { $ref: "AuthErrorResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
});

export const feedPlacesRouteSchema = defineRoute({
  summary: "Get a ranked place feed for Decide for me.",
  description:
    "Returns a card-ready ranked feed. Valid authenticated users get recommendation-service backed personalization; anonymous or cold-start requests receive a quality fallback feed.",
  query: "FeedPlacesQuery",
  ok: "FeedPlacesResponse"
});
