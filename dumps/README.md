# Data Dumps

This folder stores small MVP data files used for local development, demos, and
manual Supabase imports.

## Current Files

- `sample_with_coordinates.csv`
  - TripAdvisor sample with deterministic random Berlin coordinates.
  - Used for quick map testing.

- `raw_tripadvisor_restaurants_import.csv`
  - TripAdvisor-shaped raw/staging CSV.
  - Used as input for the TripAdvisor mapper.

- `bucharest_cafes.csv`
  - OpenStreetMap cafes dump for Bucharest.
  - Used as input for the OSM mapper.

- `tripadvisor_places.csv`
  - Generated canonical `public.places` import CSV.
  - Source: `pnpm map:tripadvisor dumps/raw_tripadvisor_restaurants_import.csv --out dumps/tripadvisor_places.csv`.

- `osm_bucharest_places.csv`
  - Generated canonical `public.places` import CSV.
  - Source: `pnpm map:osm dumps/bucharest_cafes.csv --out dumps/osm_bucharest_places.csv`.

## Rules

- Small sample/import CSVs can be committed if they are useful for rebuilding
  the MVP state.
- Large raw dumps should not be committed by default.
- Do not commit secrets, API keys, private user data, or production exports.
- Generated files should have a clear source and purpose.
- If a file is temporary, include that in the filename or remove it before
  committing.

## Future Option

If large data files start appearing here, add a local `.gitignore` such as:

```gitignore
*.csv
!sample_with_coordinates.csv
!raw_tripadvisor_restaurants_import.csv
!bucharest_cafes.csv
!*_places.csv
!README.md
```

Do not add that blindly while curated seed CSVs are still useful to commit.
