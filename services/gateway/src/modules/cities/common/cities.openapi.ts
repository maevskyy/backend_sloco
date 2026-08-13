import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { citiesSchemaRegistry } from "./cities.schemas.js";

export const citiesComponentSchemas = buildComponentSchemas(citiesSchemaRegistry);

const defineRoute = makeDefineRoute({
  tag: "Cities",
  security: false,
  errorResponses: {
    500: { $ref: "ErrorResponse#" }
  }
});

export const listCitiesRouteSchema = defineRoute({
  summary: "List catalog cities.",
  description:
    "Distinct `places.city` values that actually have places, spelled exactly as `feed.city` and `?city=` use. Sorted by placeCount descending, then name. Public.",
  ok: "CitiesResponse"
});
