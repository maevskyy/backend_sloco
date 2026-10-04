import {
  buildComponentSchemas,
  makeDefineRoute
} from "../../../config/openapi.js";
import { walksSchemaRegistry } from "./walks.schemas.js";

export const walksComponentSchemas = buildComponentSchemas(walksSchemaRegistry);

const walkError = { $ref: "WalkErrorResponse#" } as const;
const mixedError = { $ref: "WalksMixedErrorResponse#" } as const;

const defineRoute = makeDefineRoute({
  tag: "Walks",
  errorResponses: {
    400: mixedError,
    401: { $ref: "AuthErrorResponse#" },
    404: walkError,
    409: walkError,
    413: walkError,
    422: walkError,
    500: mixedError,
    503: mixedError,
    504: walkError
  }
});

const AUTH_NOTE =
  "Bearer token optional; an invalid one is 401. Errors of the planner come as {error: {code, message, params}} — branch on code (services/walk-planner/docs/messages.md).";

export const walksConfigRouteSchema = defineRoute({
  summary: "Walk form data for a city.",
  description: `Activities, styles, shapes, visit lengths, defaults, limits and versions. Build every picker from it. ${AUTH_NOTE}`,
  query: "WalksConfigQuery",
  ok: "WalksConfigResponse"
});

export const walksPlanRouteSchema = defineRoute({
  summary: "Plan walking routes.",
  description: `1–5 timed route variants for the requested activities. A signed-in user's saved places personalise the choice of places (the app never sends favourites). A valid request that finds nothing is still 200 with status no_candidates / no_route. Keep the request echo and the variant's sequence for edits. ${AUTH_NOTE}`,
  query: "WalksRouteQuery",
  body: "WalksPlanBody",
  ok: "WalksPlanResponse"
});

export const walksScheduleRouteSchema = defineRoute({
  summary: "Re-time an edited route in exactly this order.",
  description: `Send back the plan's request echo unchanged and the edited sequence (reordered, removed, restored, new dwellMin). Stateless. 409 catalog_changed → plan again with the same form. ${AUTH_NOTE}`,
  query: "WalksRouteQuery",
  body: "WalksScheduleBody",
  ok: "WalksEditResponse"
});

export const walksInsertRouteSchema = defineRoute({
  summary: "Add one place where it costs the least time, then re-time.",
  description: `Answers insertedIndex. 409 place_already_in_route; 422 place_temporarily_closed → ask the user, resend with allowTemporarilyClosed: true; 422 place_closed_forever; 404 unknown_place. ${AUTH_NOTE}`,
  query: "WalksRouteQuery",
  body: "WalksInsertBody",
  ok: "WalksEditResponse"
});

export const walksSearchRouteSchema = defineRoute({
  summary: "Search walk places by name.",
  description: `For must-visit, add-a-place and start-at-a-place pickers. Ignores case and diacritics. ${AUTH_NOTE}`,
  query: "WalksSearchQuery",
  ok: "WalksSearchResponse"
});

export const walksPlaceRouteSchema = defineRoute({
  summary: "Walk place screen.",
  description: `Card with up to 10 photos, text sections, tags and the week's opening hours. Takes the sourceId (Google CID), not places.id. ${AUTH_NOTE}`,
  params: "WalksPlaceParams",
  query: "WalksPlaceQuery",
  ok: "WalksPlaceResponse"
});
