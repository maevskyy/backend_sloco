import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { searchSchemaRegistry } from "./search.schemas.js";

export const searchComponentSchemas = buildComponentSchemas(searchSchemaRegistry);

const defineRoute = makeDefineRoute({
  tag: "Search",
  security: false,
  errorResponses: {
    400: { $ref: "ValidationErrorResponse#" },
    401: { $ref: "AuthErrorResponse#" },
    500: { $ref: "ErrorResponse#" }
  }
});

export const searchPlacesRouteSchema = defineRoute({
  summary: "Search places globally.",
  description:
    "Searches all places, not only the current map bbox. Results are fuzzy-matched by name/type/tags and softly ranked by optional user location and city/country context.",
  query: "SearchPlacesQuery",
  ok: "SearchPlacesResponse"
});
