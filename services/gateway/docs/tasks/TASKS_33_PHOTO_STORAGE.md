# Task 33: Photo Storage (R2) + storage-agnostic serving

## Goal

Move place photos off the app box into cheap blob storage behind a CDN, sized for
the real trajectory (TBs at launch, read-heavy "maps-like" traffic), with **zero
per-GB egress risk**, and wire `place_photos` to the new `cid`-keyed catalog. The
design must make a later storage move a non-event (env change + rclone), so we can
forget about this until post-MVP.

## Requirements (drive the choice)

- Read-heavy app (photos load constantly, like Google Maps). Egress is the cost
  that scales with success — it must be $0 or pennies, with **no bill-shock
  scenario** (the Supabase-egress lesson).
- Scale: **20 GB today → 1–3+ TB at launch**, growing after. Maybe video later.
- No AWS. Candidates narrowed to Cloudflare R2 and Hetzner Object Storage.
- Solo-MVP ops: one-time setup, then forget.

## Facts (measured on the prod box)

- Photos: `/opt/sloco-data/visual_photo_profiles/photos_cid/<CID>/NN_all.jpg`.
- **20 GB**, **11,483 places** (~91% of the 12,579-row catalog), **49,382 files**,
  ~4.3 photos/place, no outliers (largest place ≈ 7 MB).
- Folder name = `cid` = `places.source_id` → photos are already in the new key
  space (no ChIJ↔cid remap).
- These are **vibe** photos (`photo_source='vibe'`). Review photos are out of scope.

## Storage decision — Cloudflare R2 + Cloudflare CDN (chosen)

| | R2 + CF CDN | Hetzner Object Storage (+CF) |
|---|---|---|
| Storage | $0.015/GB → $15/TB/mo | ~€6/mo (1 TB incl.) + ~€6/extra TB |
| Egress | **$0 architecturally, at any traffic** | 1 TB incl., ~€1.19/TB after |
| 3 TB launch, read-heavy | ~$45/mo flat, traffic-independent | ~€18/mo + small egress |
| CDN | native (PoPs incl. Bucharest, Tbilisi) | none — CF in front anyway |
| Bill-shock | impossible | unlikely but nonzero |
| S3-compatible | yes | yes |

**Why R2:** for a read-heavy maps app, traffic is the one variable we don't
control; R2 removes it from the equation entirely. Storage cost is trivial at our
scale either way. Hetzner OS stays the storage-cost optimizer if we ever sit on
≥10 TB — and switching is trivial by design (below), so this choice is not
forever. (Verify current pricing at provision time.)

## Architecture — storage-agnostic (the "forget about it" part)

- `place_photos.storage_path` = **relative key**: `sloco_ai/<cid>/NN_all.jpg`.
- Gateway env **`PHOTO_BASE_URL`** (e.g. `https://photos.sloco.pp.ua`); served URL
  = `${PHOTO_BASE_URL}/${storage_path}`. The indexer writes
  `place_photos.public_url` the same way, so the existing RPCs are unchanged.
- Migrating storage later = rclone S3→S3 + flip `PHOTO_BASE_URL` (+ one
  `UPDATE public_url` or indexer re-run). No code/schema changes.
- Objects are immutable (new photo = new filename) → upload with
  `Cache-Control: public, max-age=31536000, immutable`, cache-everything at CF.

## Migration plan

**Phase 0 — provision (one-time).** Create the R2 bucket `sloco-photos` (EU
location hint) + a bucket-scoped S3 API token. For day one, enable the free
**r2.dev public subdomain** and use it as `PHOTO_BASE_URL` (rate-limited, no
CDN cache — fine for development, not for launch). Before launch, move the
`sloco.pp.ua` zone to Cloudflare DNS (⚠️ verify `pp.ua` is accepted; fallback:
buy the real launch domain) and connect custom domain `photos.sloco.pp.ua` to
the bucket — free plan, CDN cache included — then flip `PHOTO_BASE_URL`.
Cost note: CDN/egress are $0 on the free plan; the only recurring cost is R2
storage (10 GB free, then $0.015/GB → ~$0.15/mo today, ~$45/mo at 3 TB).

**Phase 1 — bulk copy today's 20 GB (from the app box, idempotent).**

```bash
rclone copy /opt/sloco-data/visual_photo_profiles/photos_cid/ \
  r2:sloco-photos/sloco_ai/ \
  --transfers 64 --checkers 128 --fast-list -P \
  --header-upload "Cache-Control: public, max-age=31536000, immutable"
rclone size r2:sloco-photos/sloco_ai    # expect ~49,382 objects / ~20 GB
```

**Phase 2 — DB wiring.** New script `scripts/photos/index-sloco-photos.ts`: walk
`photos_cid/<cid>/*.jpg` → upsert `place_photos`
(`place_source='sloco_ai'`, `place_source_id=<cid>`, `photo_source='vibe'`,
`photo_item_id=<file stem>`, `photo_index=NN`,
`storage_path='sloco_ai/<cid>/<file>'`, `public_url=${PHOTO_BASE_URL}/...`;
onConflict `(place_source, place_source_id, photo_source, photo_item_id)`).
Then backfill primaries (RPC joins `ph.storage_path = p.primary_photo_path`):

```sql
UPDATE public.places p
   SET primary_photo_path = pp.storage_path
  FROM public.place_photos pp
 WHERE pp.place_source = p.source
   AND pp.place_source_id = p.source_id
   AND pp.photo_index = 0;
```

Add `PHOTO_BASE_URL` to gateway env schema / compose / deploy secrets.

**Phase 3 — TB-scale ingestion path (launch).** New photo batches go **straight
to the bucket** from the data team's storage (`rclone` to
`r2:sloco-photos/sloco_ai/<cid>/...`), not through the app box. Re-run the
indexer (idempotent upsert) from a folder/manifest. Key convention above is the
contract. At 3 TB ≈ $45/mo storage, $0 egress; if storage cost ever dominates
(≥10 TB), rclone to Hetzner OS/B2 + flip `PHOTO_BASE_URL`.

**Phase 4 — cleanup.** Decommission the old Supabase `place-photos` bucket
(~3k ChIJ-keyed photos). Keep the app-box folder as backup for a few weeks, then
delete.

## Out Of Scope (later)

- Thumbnails / on-the-fly resize (CF Images / imgproxy) — the big bandwidth lever
  once there are real users; post-MVP.
- Review photos and the ~240 GB raw source trees.
- Video pipeline (CF Stream if/when needed; note CF free-tier caches files ≤512 MB).
- Signed URLs / private buckets (photos are public content).

## Verification

- `rclone size` == 49,382 objects ≈ 20 GB; spot-open a CDN URL.
- `select count(*) from place_photos where place_source='sloco_ai';` ≈ 49,382.
- `select count(*) from places where primary_photo_path is not null;` ≈ 11,483.
- Map/feed/place cards return working `PHOTO_BASE_URL` photo URLs; place without
  photos → null, no error.
- Flip test: point `PHOTO_BASE_URL` at the app-box origin and back — delivery
  switches with zero other changes.

## Open Decisions (confirm on approval)

1. **R2 + CF CDN** as the origin — confirm (alternative: Hetzner OS, ~3× cheaper
   storage at multi-TB, nonzero egress; switchable later either way).
2. CDN hostname: `photos.sloco.pp.ua` (requires the zone on Cloudflare — verify
   `pp.ua`; fallback: buy the real launch domain now).
