import { z } from "zod";

export const reactionSchema = z.enum(["favorite", "dislike", "hide"]);

export const reactionParamsSchema = z.object({
  placeId: z.coerce.number().int().min(1)
});

export const setReactionBodySchema = z.object({
  reaction: reactionSchema
});

export const setReactionResponseSchema = z.object({
  placeId: z.number().int(),
  reaction: reactionSchema
});

export const reactionsResponseSchema = z.object({
  favorites: z.array(z.number().int()),
  dislikes: z.array(z.number().int()),
  hidden: z.array(z.number().int())
});

export const reactionsSchemaRegistry = z.registry<{ id: string }>();

reactionsSchemaRegistry.add(reactionParamsSchema, { id: "ReactionParams" });
reactionsSchemaRegistry.add(setReactionBodySchema, { id: "SetReactionBody" });
reactionsSchemaRegistry.add(setReactionResponseSchema, {
  id: "SetReactionResponse"
});
reactionsSchemaRegistry.add(reactionsResponseSchema, {
  id: "ReactionsResponse"
});

export type PlaceReaction = z.infer<typeof reactionSchema>;
export type ReactionParams = z.infer<typeof reactionParamsSchema>;
export type SetReactionBody = z.infer<typeof setReactionBodySchema>;
export type SetReactionResponse = z.infer<typeof setReactionResponseSchema>;
export type ReactionsResponse = z.infer<typeof reactionsResponseSchema>;
