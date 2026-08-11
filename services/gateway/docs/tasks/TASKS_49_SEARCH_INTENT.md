# TASKS 49: Search finds names, not intent — "coffee" does not find cafés

**Status: Planned — needs a product decision (options below).**

Raised by Kirill 2026-08-12: *"если я хочу просто попить кофе, я пишу в поиске кофе —
оно найдёт кофейни или только те заведения, у которых в названии есть кофе?"*

Measured live the same day, Bucharest centre. The answer is **only the names**.

## Evidence

| query | results | matched by name | actually a café by kind | другой город (>100 km) |
|---|---|---|---|---|
| `coffee` | 15 | **15/15** | 13 | 7 |
| `cafe` | 15 | 13/15 | 4 | 11 |
| `cafenea` (RO) | 15 | 15/15 | 11 | 0 |
| `pizza` | 15 | 15/15 | — | 2 |
| **`кофе` (RU)** | **0** | — | — | — |
| **`пицца` (RU)** | 3 | 0 | — | 3 (noise) |

`matchReason` was `name` on **every single result of every query**. The top hit for
`coffee` is a place literally named "Coffee" — a *Restaurant* in Tbilisi, 1 545 km away.

Sharper test: take the five real cafés within 1.5 km (`?category=cafe&radiusMeters=1500`)
— Coffee Break, Starbucks, Cool Drip Cafe, Santhe Fitoceainarie, Panion — and search
`coffee` with a 20 km radius. **1 of 5 is found** (the one with "Coffee" in its name).

## Why

`search_places` ranks `100·text_match + 30·exact_name + …`, where `text_match` is a
**trigram** similarity against the name and `search_keywords`. Trigrams compare spelling,
not meaning: "coffee" vs the venue kind "Cafe" scores below the 0.3 threshold, so a
perfect café named "Origo" is not a candidate at all. A place *named* "Coffee" scores
~130 and wins from any distance — the proximity term is worth at most 10 points.

Two independent gaps:

1. **No intent → kind mapping.** The query word and the venue kind are different
   vocabularies ("coffee" ≠ "Cafe", "beer" ≠ "Bar", "фильм" ≠ "Movie theater").
2. **No language mapping.** The catalog is English kinds + local names (RO/GE). Cyrillic
   queries match nothing; the three hits for `пицца` are accidental noise from
   Russian-named Tbilisi places.

## Already available today (no backend work)

`radiusMeters` (shipped in `TASKS_45`) applies to text search too, and fixes the
cross-city half immediately: `?q=coffee&radiusMeters=20000` drops foreign-city results
from 4/10 to **0/10**. **Tell iOS to send it on every text search.** This does not fix
intent — it only stops the answer being wrong *and* far away.

## Options (product decision)

**A. Intent dictionary → bucket routing (recommended).** A small synonym table in the
gateway maps query words to the seven buckets we already have:
`coffee|кофе|кафе|cafenea|espresso|latte → cafe`, `beer|пиво|бар → bar`,
`pizza|пицца → food`, `кино|film → culture`, … When a query hits the dictionary, the
request runs as text **∪** bucket-near-me and merges the two lists (name matches first,
then nearby places of that kind). Multilingual by construction — add a language by adding
words. Gateway-only, no migration, ~half a day. Reuses `place-buckets.ts`.
Limit: only covers words we list.

**B. Synonyms baked into `search_keywords`.** Extend the keyword trigger so each place's
searchable text carries synonyms of its kind ("Cafe" → `cafe coffee espresso кофе кафе`).
Then today's single ranking formula just works, no special-casing in the app layer.
Costs a migration + full backfill, and the synonym list lives in SQL where it is harder to
iterate. Same coverage limit as A.

**C. Semantic search.** Postgres FTS with real dictionaries, or — more interesting — reuse
the recommendation service, which already holds text embeddings for all 12 578 places
(`location_embeddings_combined_food_ttd`). A `/v1/search/semantic` that embeds the query
and does a vector top-N would answer "тихое место поработать с кофе" properly. Biggest
win, biggest scope: a new endpoint, embedding the query at request time, and a latency
budget. A separate project, not a fix.

**Recommendation: A now** (it is cheap, multilingual, and reuses what `TASKS_45` built),
plus telling iOS to send `radiusMeters` today. Revisit C once the search UX is otherwise
settled — the embeddings are already sitting there.

## Out of scope

- Ranking-weight tuning (raising the proximity term) — a blunt instrument that does not
  solve intent; revisit after A.
- The short-query latency issue (`q=co` ≈ 1 s) — `TASKS_48`.
