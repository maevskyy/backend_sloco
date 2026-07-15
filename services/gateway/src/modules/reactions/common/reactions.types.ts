import type { z } from "zod";
import type {
  reactionsResponseSchema,
  reactionSchema,
  setReactionResponseSchema
} from "./reactions.schemas.js";

export type PlaceReaction = z.infer<typeof reactionSchema>;
export type SetReactionResult = z.infer<typeof setReactionResponseSchema>;
export type ReactionsResult = z.infer<typeof reactionsResponseSchema>;

export type PlaceSourceIdRow = {
  id: number;
  source_id: string;
};

export type PlaceReactionRow = {
  source_id: string;
  reaction: PlaceReaction;
};

export type ReactionsStoreContract = {
  setReaction(
    userId: string,
    placeId: number,
    reaction: PlaceReaction
  ): Promise<void>;
  deleteReaction(userId: string, placeId: number): Promise<void>;
  listReactions(userId: string): Promise<ReactionsResult>;
  getReactions(
    userId: string,
    placeIds: number[]
  ): Promise<Map<number, PlaceReaction>>;
};

export type ReactionsServiceContract = {
  setReaction(
    userId: string,
    placeId: number,
    reaction: PlaceReaction
  ): Promise<SetReactionResult>;
  deleteReaction(userId: string, placeId: number): Promise<void>;
  getReactions(userId: string): Promise<ReactionsResult>;
  getReactionMap(
    userId: string,
    placeIds: number[]
  ): Promise<Map<number, PlaceReaction>>;
};
