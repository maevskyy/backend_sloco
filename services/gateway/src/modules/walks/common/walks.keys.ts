// Key conversion between the app (camelCase) and walk-planner (snake_case),
// services/walk-planner/docs/INTEGRATION.md §2.1. Generic and recursive on
// purpose — no field whitelist: a later v1 of the service may add fields, and
// an edit must send the request echo and the sequence back intact. Only keys
// change, never values (enum codes such as `on_the_way` stay as they are).

// A walk place id is the Google CID = `places.source_id`; the app also gets
// `placeId` (= `places.id`) next to it, so the CID is called `sourceId`.
const TO_CAMEL_EXCEPTIONS: Record<string, string> = {
  place_id: "sourceId",
  must_visit_place_ids: "mustVisitSourceIds",
  favourite_place_ids: "favouriteSourceIds",
  want_to_go_place_ids: "wantToGoSourceIds"
};

const TO_SNAKE_EXCEPTIONS: Record<string, string> = Object.fromEntries(
  Object.entries(TO_CAMEL_EXCEPTIONS).map(([snake, camel]) => [camel, snake])
);

// Values passed through untouched, nested objects included: message and
// error `params` stay snake_case (they match the service's messages.json),
// and `geometry` is GeoJSON.
const VERBATIM_KEYS = new Set(["params", "geometry"]);

// Added by the gateway to stops and cards; the service has no such field.
const GATEWAY_ONLY_KEYS = new Set(["placeId"]);

export function toCamel(value: unknown): unknown {
  return convert(value, (key) => TO_CAMEL_EXCEPTIONS[key] ?? snakeToCamel(key));
}

export function toSnake(value: unknown): unknown {
  return convert(
    value,
    (key) => TO_SNAKE_EXCEPTIONS[key] ?? camelToSnake(key),
    GATEWAY_ONLY_KEYS
  );
}

function convert(
  value: unknown,
  renameKey: (key: string) => string,
  dropKeys?: ReadonlySet<string>
): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => convert(item, renameKey, dropKeys));
  }

  if (value === null || typeof value !== "object") {
    return value;
  }

  const result: Record<string, unknown> = {};

  for (const [key, item] of Object.entries(value)) {
    if (dropKeys?.has(key)) continue;

    // Data keys (the ids of `places{}`) are digits: both renames leave them as is.
    result[renameKey(key)] = VERBATIM_KEYS.has(key)
      ? item
      : convert(item, renameKey, dropKeys);
  }

  return result;
}

function snakeToCamel(key: string) {
  return key.replace(/_([a-z0-9])/g, (_match, char: string) =>
    char.toUpperCase()
  );
}

function camelToSnake(key: string) {
  return key.replace(/[A-Z]/g, (char) => `_${char.toLowerCase()}`);
}
