import { buildComponentSchemas, makeDefineRoute } from "../../../config/openapi.js";
import { sharedErrorResponses } from "../../../config/http-schemas.js";
import { reactionsSchemaRegistry } from "./reactions.schemas.js";

export const reactionsComponentSchemas = buildComponentSchemas(
  reactionsSchemaRegistry
);

const defineRoute = makeDefineRoute({
  tag: "Reactions",
  errorResponses: sharedErrorResponses
});

export const getReactionsRouteSchema = defineRoute({
  summary: "Get place reactions for the authenticated user.",
  description:
    "Returns the user's reacted places grouped by mutually-exclusive reaction type.",
  ok: "ReactionsResponse"
});

export const setReactionRouteSchema = defineRoute({
  summary: "Set a place reaction.",
  description:
    "Upserts one mutually-exclusive favorite, dislike, or hide reaction for the authenticated user and place.",
  params: "ReactionParams",
  body: "SetReactionBody",
  ok: "SetReactionResponse"
});

export const deleteReactionRouteSchema = {
  tags: ["Reactions"],
  summary: "Delete a place reaction.",
  description:
    "Deletes any existing reaction for the authenticated user and place. The operation is idempotent.",
  security: [{ bearerAuth: [] }],
  params: {
    $ref: "ReactionParams#"
  },
  response: {
    204: {
      description: "Reaction deleted."
    },
    ...sharedErrorResponses
  }
} as const;
