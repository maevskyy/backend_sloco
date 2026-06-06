import { sharedErrorResponses } from "../../../config/http-schemas.js";
import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { placesSchemaRegistry } from "./places.schemas.js";

export const placesComponentSchemas =
  buildComponentSchemas(placesSchemaRegistry);

const defineRoute = makeDefineRoute({
  tag: "Places",
  security: false,
  errorResponses: sharedErrorResponses
});

export const getPlaceDetailsRouteSchema = defineRoute({
  summary: "Get place details.",
  description:
    "Returns the detailed place payload for a selected map pin. Auth is optional; valid auth enriches the response with saved state.",
  params: "PlaceDetailsParams",
  ok: "PlaceDetailsResponse"
});
