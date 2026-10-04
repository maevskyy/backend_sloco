import { z } from "zod";

// The walk contract is walk-planner's (services/walk-planner/docs/API.md),
// converted to camelCase. The gateway checks only what it must read itself —
// query types and the sourceId path segment; every range and domain rule
// (window, slots, radius, limits…) is the service's, answered as
// `{error: {code, message, params}}` that the app branches on by `code`.
// The body and response schemas below document the main fields for Swagger;
// they are loose, so fields a later v1 adds pass through untouched.

export const walkLangSchema = z.enum(["ru", "en"]);
export const walkGeometrySchema = z.enum(["geojson", "polyline6"]);

const queryBoolean = z.preprocess(
  (value) => (value === "true" ? true : value === "false" ? false : value),
  z.boolean()
);

export const walksConfigQuerySchema = z.object({
  city: z.string().optional(),
  lang: walkLangSchema.optional()
});

export const walksRouteQuerySchema = z.object({
  lang: walkLangSchema.optional(),
  geometry: walkGeometrySchema
    .optional()
    .describe("Segment geometry; polyline6 is the compact one for the app.")
});

export const walksSearchQuerySchema = z.object({
  q: z.string().describe("Name text, 1–200 characters; the address matches too."),
  city: z.string().optional(),
  lat: z.coerce.number().optional(),
  lon: z.coerce.number().optional(),
  limit: z.coerce.number().int().optional().describe("1–50, default 20."),
  includeClosed: queryBoolean
    .optional()
    .describe("Also places Google marks closed_forever."),
  lang: walkLangSchema.optional()
});

export const walksPlaceParamsSchema = z.object({
  sourceId: z
    .string()
    .regex(/^[0-9]{1,20}$/)
    .describe("Google CID as a decimal string (= places.source_id).")
});

export const walksPlaceQuerySchema = z.object({
  city: z.string().optional(),
  lang: walkLangSchema.optional()
});

// Any JSON object; the service validates it.
export const walksBodySchema = z.record(z.string(), z.unknown());

const sourceId = z
  .string()
  .describe("Google CID as a decimal string; never a JSON number.");

const sourceIdList = z.array(sourceId);

export const walksPlanBodySchema = z.looseObject({
  city: z.string().optional(),
  date: z.string().describe("YYYY-MM-DD, city-local."),
  startTime: z.string().optional().describe("HH:MM, city-local."),
  endTime: z.string().optional().describe("HH:MM, city-local."),
  endDayOffset: z.number().int().optional(),
  shape: z.enum(["loop", "one_way", "free"]).optional(),
  start: z
    .unknown()
    .optional()
    .describe('{lat, lon} | {sourceId} | "city_center"; required for loop and one_way.'),
  style: z.enum(["max", "chill", "scenic"]).optional(),
  slots: z
    .array(
      z.looseObject({
        activity: z.string(),
        dwellMin: z.number().int().nullable().optional()
      })
    )
    .optional()
    .describe("In walk order, at most 8, each activity once."),
  mustVisitSourceIds: sourceIdList.optional(),
  variants: z.number().int().optional(),
  radiusKm: z.number().optional(),
  fillWindow: z.boolean().optional(),
  knownHoursOnly: z.boolean().optional(),
  lang: walkLangSchema.optional(),
  geometry: walkGeometrySchema.optional()
});

export const walksScheduleBodySchema = z.looseObject({
  request: z
    .record(z.string(), z.unknown())
    .describe("The request echo of the plan response, unchanged."),
  sequence: z
    .array(z.record(z.string(), z.unknown()))
    .describe("A variant's sequence: reordered, removed, restored or re-timed."),
  variantIndex: z.number().int().optional(),
  lang: walkLangSchema.optional(),
  geometry: walkGeometrySchema.optional()
});

export const walksInsertBodySchema = walksScheduleBodySchema.extend({
  sourceId,
  allowTemporarilyClosed: z.boolean().optional(),
  dwellMin: z.number().nullable().optional()
});

// Responses: declared fields are documentation only — optional and
// unknown-typed, so the serializer never drops, rejects or demands what the
// service sends (a 200 with status no_route has no variants to speak of).
const field = (description?: string) =>
  description ? z.unknown().optional().describe(description) : z.unknown().optional();

export const walksConfigResponseSchema = z.looseObject({
  city: field(),
  timezone: field(),
  activities: field("Codes with label, labelRu, labelEn, group, baseDwellMin."),
  styles: field(),
  shapes: field(),
  dwellChoices: field(),
  defaults: field(),
  limits: field(),
  versions: field()
});

export const walksPlanResponseSchema = z.looseObject({
  planId: field(),
  versions: field(),
  status: field("ok | no_candidates | no_route (still HTTP 200)."),
  request: field("Normalised request echo — send it back with edits as is."),
  personalization: field(),
  messages: field(),
  variants: field(
    "Each with summary, messages, stops (sourceId + placeId), segments, navigation, bbox, sequence."
  ),
  places: field(
    "Cards keyed by sourceId; each card carries placeId (places.id or null)."
  )
});

export const walksEditResponseSchema = z.looseObject({
  versions: field(),
  request: field(),
  variant: field(),
  places: field(),
  messages: field(),
  insertedIndex: field("Only from /insert.")
});

export const walksSearchResponseSchema = z.looseObject({
  city: field(),
  query: field(),
  count: field(),
  results: field("Each with sourceId, placeId, name, match, distanceM…"),
  catalogVersion: field()
});

export const walksPlaceResponseSchema = z.looseObject({
  sourceId: field(),
  placeId: field(),
  name: field(),
  photos: field(),
  sections: field(),
  openingHoursWeek: field()
});

export const walkErrorResponseSchema = z.object({
  error: z.looseObject({
    code: z
      .string()
      .describe("Stable; branch on it. Unknown codes: go by HTTP status."),
    message: z.string(),
    params: field()
  })
});

// Statuses both the gateway and the service answer: 400 (gateway query check
// or service bad_request), 500, 503 (database unavailable, or the service's
// busy / not_ready / walk_planner_unavailable). Either shape serializes.
export const walksMixedErrorResponseSchema = z.looseObject({
  error: field(
    "Walk envelope {code, message, params} — from the service or walk_planner_*."
  ),
  status: field('Gateway envelope: "error".'),
  message: field(),
  issues: field()
});

export type WalksConfigQuery = z.infer<typeof walksConfigQuerySchema>;
export type WalksRouteQuery = z.infer<typeof walksRouteQuerySchema>;
export type WalksSearchQuery = z.infer<typeof walksSearchQuerySchema>;
export type WalksPlaceQuery = z.infer<typeof walksPlaceQuerySchema>;
export type WalksBody = z.infer<typeof walksBodySchema>;

export const walksSchemaRegistry = z.registry<{ id: string }>();

walksSchemaRegistry.add(walksConfigQuerySchema, { id: "WalksConfigQuery" });
walksSchemaRegistry.add(walksRouteQuerySchema, { id: "WalksRouteQuery" });
walksSchemaRegistry.add(walksSearchQuerySchema, { id: "WalksSearchQuery" });
walksSchemaRegistry.add(walksPlaceParamsSchema, { id: "WalksPlaceParams" });
walksSchemaRegistry.add(walksPlaceQuerySchema, { id: "WalksPlaceQuery" });
walksSchemaRegistry.add(walksPlanBodySchema, { id: "WalksPlanBody" });
walksSchemaRegistry.add(walksScheduleBodySchema, { id: "WalksScheduleBody" });
walksSchemaRegistry.add(walksInsertBodySchema, { id: "WalksInsertBody" });
walksSchemaRegistry.add(walksConfigResponseSchema, {
  id: "WalksConfigResponse"
});
walksSchemaRegistry.add(walksPlanResponseSchema, { id: "WalksPlanResponse" });
walksSchemaRegistry.add(walksEditResponseSchema, { id: "WalksEditResponse" });
walksSchemaRegistry.add(walksSearchResponseSchema, {
  id: "WalksSearchResponse"
});
walksSchemaRegistry.add(walksPlaceResponseSchema, { id: "WalksPlaceResponse" });
walksSchemaRegistry.add(walkErrorResponseSchema, { id: "WalkErrorResponse" });
walksSchemaRegistry.add(walksMixedErrorResponseSchema, {
  id: "WalksMixedErrorResponse"
});
