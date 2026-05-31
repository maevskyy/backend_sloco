# TBD: Place Photos Integration

## Summary

Future strategy for place photos in the MVP.

Curated photos should come as a CSV manifest exported from Excel or Google
Sheets, plus a zip archive with the image files. A backend import script will
generate normalized WebP variants, upload originals and variants to Supabase
Storage, and link photo metadata to `public.places`.

This document is planning-only. It does not add migrations, code, dependencies,
or API changes.

## Storage Decision

- Use Supabase Storage first.
- Use a private bucket for original files.
- Use a public bucket for generated variants.
- Do not use Supabase Image Transformations at the start.
- Generate variants ourselves to keep cost and output predictable.

Planned buckets:

```text
place-photo-originals   private archive bucket
place-photo-variants    public app-serving bucket
```

## Input Contract

Friends provide:

- one CSV export from Excel or Google Sheets;
- one zip archive with the referenced image files.

Required CSV columns:

```text
source,source_id,filename
```

Optional CSV columns:

```text
source_photo_id,position,is_primary,alt_text,attribution,license
```

Rules:

- `source + source_id` must match `public.places(source, source_id)`.
- `filename` must exactly match a file in the unzipped photos folder.
- One CSV row equals one photo.
- Multiple photos for one place use multiple rows with the same
  `source + source_id`.
- If `source_photo_id` is empty, use the filename without extension.

Recommended file guidance for friends:

- prefer `jpg`, `jpeg`, or `png`;
- avoid `heic`, tiny thumbnails, screenshots with UI, and heavily compressed
  files;
- use stable filenames with no duplicates;
- aim for at least about `1200px` on the long side.

## Processing Strategy

The future import script should:

- validate the CSV;
- check that every image file exists;
- check that every `source + source_id` exists in `public.places`;
- upload the untouched original to the private bucket;
- strip metadata from public variants;
- generate WebP variants with `sharp`;
- upsert photo metadata into `public.place_photos`;
- be idempotent when rerun for the same manifest.

Planned variants:

```text
thumb.webp   small previews
card.webp    map/list cards
hero.webp    place detail hero
```

Path strategy:

```text
place-photo-originals/{source}/{placeKey}/{photoKey}/original.{ext}
place-photo-variants/{source}/{placeKey}/{photoKey}/thumb.webp
place-photo-variants/{source}/{placeKey}/{photoKey}/card.webp
place-photo-variants/{source}/{placeKey}/{photoKey}/hero.webp
```

Recommended deterministic keys:

```text
placeKey = sha1(source + ":" + source_id).slice(0, 16)
photoKey = sanitized source_photo_id
```

## API Direction

Later, `GET /v1/map/places` should return a nullable `primaryPhoto`.

Example future shape:

```json
{
  "primaryPhoto": {
    "id": 123,
    "urls": {
      "thumb": "https://.../thumb.webp",
      "card": "https://.../card.webp",
      "hero": "https://.../hero.webp"
    },
    "width": 800,
    "height": 600,
    "altText": "Cafe interior",
    "attribution": "Photo by ..."
  }
}
```

If no photo exists:

```json
{
  "primaryPhoto": null
}
```

Originals must not be exposed by public API. They are only an archive for future
regeneration.

Gallery and place detail photos should be a separate future task.

## Assumptions

- Supabase Pro may be needed once photo storage or egress exceeds Free limits.
- No frontend direct upload in the first implementation.
- No user-generated photos in the first implementation.
- Copyright and attribution are handled by curated input.
- Real implementation starts when we have a first CSV + zip sample.
