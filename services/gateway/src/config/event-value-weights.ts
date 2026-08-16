import { z } from "zod";
import rawEventValueWeights from "./event-value-weights.json" with { type: "json" };

// The action "price list" (event-log spec Part 3): a manual static weight per
// event type. Weights are NEVER written into events — they are applied at read
// time, so one config diff re-values the whole history. Changing a weight is a
// dedicated one-line PR to the JSON file; git is the audit trail. The version
// string is stamped into every rec_served row.
const eventValueWeightsSchema = z.object({
  version: z.string().min(1),
  weights: z.record(z.string(), z.number())
});

export type EventValueWeights = z.infer<typeof eventValueWeightsSchema>;

export const eventValueWeights: EventValueWeights =
  eventValueWeightsSchema.parse(rawEventValueWeights);
