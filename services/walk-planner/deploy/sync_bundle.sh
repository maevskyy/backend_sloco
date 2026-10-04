#!/usr/bin/env bash
# sync_bundle.sh: upload a built walk-planner data bundle to the server: verified, atomic, never overwritten.
#
# Usage:  SLOCO_SSH=deploy@host deploy/sync_bundle.sh <bundle_dir> [--dest DIR] [--validate] [--dry-run]
#         SLOCO_SSH=deploy@host deploy/sync_bundle.sh --photos [--dry-run]
#   <bundle_dir>  output of `python -m walk_planner bundle build`: manifest.json inside, and normally named
#                 after its bundle_id (the server copy is always named <bundle_id>)
#   --dest DIR    server bundles root (default /opt/sloco-data/walk/bundles, mounted read-only by compose)
#   --validate    first run `python -m walk_planner bundle validate <bundle_dir>` locally (full schema check;
#                 needs the package's dependencies in $PYTHON)
#   --dry-run     show what would be transferred; change nothing on the server
#   --photos      sync the card-photo tree instead of a bundle (see "Photos" below)
#
# Bundle steps:
#   1. local: manifest.json parses, bundle_id is sane, every file in manifest.files exists with the recorded
#      bytes + sha256. A half-written or hand-edited bundle never ships.
#   2. server: if <dest>/<bundle_id> already exists, it is checked against the manifest: identical -> exit 0,
#      different -> exit 4. Bundles are immutable: build a new one instead of patching.
#   3. rsync to <dest>/.incoming-<bundle_id>/ (resumable), `sha256sum -c` there, dirs 755 / files 444 (the
#      service runs as uid 10001), then one `mv -T` to <dest>/<bundle_id>/. The service never sees a partial
#      bundle.
#   4. list the bundles on the server and print how to activate this one.
# Activate: set WALK_BUNDLE_ID=<bundle_id> in /opt/sloco-data/walk/walk.env, then
#   docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml up -d walk-planner
# Roll back = the previous WALK_BUNDLE_ID. Keep about 3 bundles (~60 MB each); delete older ones by hand.
#
# Photos: cards carry keys photos_cid/<cid>/<NN>_<label>.jpg. They resolve only if PHOTO_BASE_URL serves the tree
# (deploy/photos.nginx.conf.example). The research repo's deploy/sync_data_to_server.sh already ships it to
# /opt/sloco-data/visual_photo_profiles/photos_cid. --photos runs the same incremental rsync (--size-only;
# about 20 GB the first time) from SLOCO_PHOTOS_SRC.
#
# Env: SLOCO_SSH (required, user@host) · SSH_OPTS (extra ssh options, e.g. "-p 2222 -i ~/.ssh/deploy")
#      PYTHON (local python3, default python3) · SLOCO_PHOTOS_SRC (default: the research repo's
#      recommendation_system/ai_location_recommender/data/visual_photo_profiles/photos_cid)
#      SLOCO_PHOTOS_DEST (default /opt/sloco-data/visual_photo_profiles)
# Server needs: bash, coreutils (sha256sum, mv -T), rsync. Exit codes: 0 ok / already there · 2 usage ·
#   3 local bundle invalid · 4 server copy differs or verification failed · 5 ssh/rsync failed or --dest missing

set -euo pipefail

log() { printf '%s [sync_bundle] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { local code=$1; shift; log "ERROR: $*"; exit "$code"; }
usage() { awk 'NR == 1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "$HERE/.." && pwd)"                      # services/walk_planner
REPO_ROOT="$(cd "$PKG_ROOT/../.." && pwd)"              # research repo (only for the photo default)
PYTHON="${PYTHON:-python3}"
DEST="/opt/sloco-data/walk/bundles"
BUNDLE=""
VALIDATE=0
DRY=0
PHOTOS=0
EXCLUDES=(--exclude '._*' --exclude '.DS_Store' --exclude '__pycache__')

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) [ $# -ge 2 ] || die 2 "--dest needs a value"; DEST=$2; shift 2 ;;
        --validate) VALIDATE=1; shift ;;
        --dry-run) DRY=1; shift ;;
        --photos) PHOTOS=1; shift ;;
        -h | --help) usage; exit 0 ;;
        -*) die 2 "unknown option: $1 (see --help)" ;;
        *) [ -z "$BUNDLE" ] || die 2 "only one bundle dir per run"; BUNDLE=$1; shift ;;
    esac
done
SSH_TARGET="${SLOCO_SSH:-}"
[ -n "$SSH_TARGET" ] || die 2 "set SLOCO_SSH=user@host"
case "$DEST" in /*) ;; *) die 2 "--dest must be an absolute server path" ;; esac
SSH_OPTS="${SSH_OPTS:-}"
# shellcheck disable=SC2086  # SSH_OPTS is a list of ssh options on purpose
remote() { ssh $SSH_OPTS "$SSH_TARGET" "$@"; }
RSYNC_E="ssh $SSH_OPTS"
q() { printf '%q' "$1"; }                               # quote one word for the remote shell

# ------------------------------------------------------------------------------------------------- photos
if [ "$PHOTOS" -eq 1 ]; then
    [ -z "$BUNDLE" ] || die 2 "--photos takes no bundle dir"
    SRC="${SLOCO_PHOTOS_SRC:-$REPO_ROOT/recommendation_system/ai_location_recommender/data/visual_photo_profiles/photos_cid}"
    PDEST="${SLOCO_PHOTOS_DEST:-/opt/sloco-data/visual_photo_profiles}"
    [ -d "$SRC" ] || die 2 "photo tree not found: $SRC (set SLOCO_PHOTOS_SRC)"
    SRC="${SRC%/}"
    [ "$(basename "$SRC")" = "photos_cid" ] || die 2 "SLOCO_PHOTOS_SRC must be a directory named photos_cid (keys start with photos_cid/)"
    log "photos: $SRC -> $SSH_TARGET:$PDEST/photos_cid (incremental, --size-only)"
    DRYFLAGS=()
    if [ "$DRY" -eq 1 ]; then DRYFLAGS=(-n --itemize-changes); fi
    remote "mkdir -p $(q "$PDEST")" || die 5 "ssh failed"
    rsync -a --size-only --partial ${DRYFLAGS[@]+"${DRYFLAGS[@]}"} "${EXCLUDES[@]}" -e "$RSYNC_E" "$SRC" "$SSH_TARGET:$PDEST/" \
        || die 5 "rsync failed"
    if [ "$DRY" -eq 0 ]; then
        remote "chmod -R a+rX $(q "$PDEST/photos_cid")" || die 5 "ssh failed (chmod)"
    fi
    log "photos done. Serve them read-only for PHOTO_BASE_URL: deploy/photos.nginx.conf.example"
    exit 0
fi

# ------------------------------------------------------------------------------------------------- 1. local checks
[ -n "$BUNDLE" ] || die 2 "usage: SLOCO_SSH=user@host $0 <bundle_dir> (see --help)"
[ -d "$BUNDLE" ] || die 2 "not a directory: $BUNDLE"
[ -f "$BUNDLE/manifest.json" ] || die 3 "$BUNDLE has no manifest.json (not a bundle?)"
command -v "$PYTHON" >/dev/null 2>&1 || die 2 "python not found: $PYTHON (set PYTHON)"
BUNDLE="$(cd "$BUNDLE" && pwd)"

if [ "$VALIDATE" -eq 1 ]; then
    log "validating with the package: python -m walk_planner bundle validate"
    PYTHONPATH="$PKG_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m walk_planner bundle validate "$BUNDLE" \
        || die 3 "bundle validate failed (or the walk_planner CLI/deps are not available to $PYTHON)"
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sync_bundle.XXXXXX")"
trap 'rm -rf -- "$TMP"' EXIT
SUMS="$TMP/SHA256SUMS"
log "checking $BUNDLE against its manifest (sha256 of every file) ..."
cat >"$TMP/check_bundle.py" <<'PY'
import hashlib, json, os, re, sys

root, out = sys.argv[1], sys.argv[2]


def fail(msg):
    print("bundle check failed: " + msg, file=sys.stderr)
    sys.exit(3)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


try:
    manifest = json.load(open(os.path.join(root, "manifest.json"), encoding="utf-8"))
except ValueError as e:
    fail("manifest.json is not valid JSON: %s" % e)
bundle_id = manifest.get("bundle_id")
if not isinstance(bundle_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", bundle_id):
    fail("manifest bundle_id missing or unusable as a directory name: %r" % (bundle_id,))
files = manifest.get("files")
if not isinstance(files, dict) or not files:
    fail("manifest.files is missing or empty")
# every file the planner reads must be listed (and so hashed): the service refuses the bundle otherwise
# (walk_planner.bundle.layout_problems); interest.dir must be "interest"
required = ["walk_catalog.parquet"]
interest = manifest.get("interest")
if interest:
    if not isinstance(interest, dict) or interest.get("dir", "interest") != "interest":
        fail("manifest.interest.dir must be 'interest'")
    required += ["interest/" + n for n in ("text_f16.npy", "image_f16.npy", "has_image.npy", "csls_density.npy",
                                           "features.parquet", "interest_meta.json")]
unlisted = [r for r in required if r not in files]
if unlisted:
    fail("manifest.files does not list %s" % ", ".join(unlisted))
real_root = os.path.realpath(root)
lines, listed = [], {"manifest.json"}
for rel, info in sorted(files.items()):
    if not re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", rel) or ".." in rel.split("/"):
        fail("unsafe or unusual path in manifest.files: %r" % rel)
    path = os.path.join(root, rel)
    if os.path.commonpath([real_root, os.path.realpath(path)]) != real_root:
        fail("%s resolves outside the bundle (symlink)" % rel)
    if not os.path.isfile(path):
        fail("listed file missing: %s" % rel)
    info = info if isinstance(info, dict) else {}
    want_sha, want_bytes = info.get("sha256"), info.get("bytes")
    if not isinstance(want_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", want_sha):
        fail("no sha256 recorded for %s" % rel)
    size = os.path.getsize(path)
    if want_bytes is not None and size != want_bytes:
        fail("%s is %d bytes, the manifest says %s" % (rel, size, want_bytes))
    got = sha256(path)
    if got != want_sha:
        fail("%s: sha256 %s, the manifest says %s" % (rel, got[:12], want_sha[:12]))
    lines.append("%s  %s" % (got, rel))
    listed.add(rel)
lines.append("%s  manifest.json" % sha256(os.path.join(root, "manifest.json")))
extra = []
for dirpath, dirnames, filenames in os.walk(root):
    for name in filenames:
        rel = os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")
        if name == ".DS_Store" or name.startswith("._") or "__pycache__" in rel.split("/"):
            continue
        if rel not in listed:
            extra.append(rel)
if extra:
    print("warning: files not listed in the manifest (shipped, but not verified): %s" % ", ".join(sorted(extra)[:10]),
          file=sys.stderr)
with open(out, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print(bundle_id)
PY
BUNDLE_ID=$("$PYTHON" "$TMP/check_bundle.py" "$BUNDLE" "$SUMS") || die 3 "local bundle check failed (see above)"
NFILES=$(wc -l <"$SUMS" | tr -d ' ')
SIZE=$(du -sh "$BUNDLE" | awk '{print $1}')
log "bundle $BUNDLE_ID: $NFILES files verified locally ($SIZE)"
if [ "$(basename "$BUNDLE")" != "$BUNDLE_ID" ]; then
    log "note: local dir is named $(basename "$BUNDLE"); the server copy will be named $BUNDLE_ID"
fi

FINAL="$DEST/$BUNDLE_ID"
INCOMING="$DEST/.incoming-$BUNDLE_ID"

# ------------------------------------------------------------------------------------------------- 2. already there?
STATE=$(remote "if [ -e $(q "$FINAL") ]; then echo exists; elif [ -d $(q "$DEST") ] && [ -w $(q "$DEST") ]; then echo absent; else echo nodest; fi") \
    || die 5 "ssh $SSH_TARGET failed"
case "$STATE" in
    nodest) die 5 "$DEST is missing or not writable on $SSH_TARGET (once: sudo install -d -m 755 -o <user> $DEST)" ;;
    exists)
        log "$FINAL already exists on the server: verifying it ..."
        if [ "$DRY" -eq 1 ]; then log "--dry-run: not verifying the server copy"; exit 0; fi
        rsync -a -e "$RSYNC_E" "$SUMS" "$SSH_TARGET:$DEST/.verify-$BUNDLE_ID.sha256" || die 5 "rsync failed"
        if remote "cd $(q "$FINAL") && sha256sum --quiet --strict -c $(q "$DEST/.verify-$BUNDLE_ID.sha256"); rc=\$?; rm -f $(q "$DEST/.verify-$BUNDLE_ID.sha256"); exit \$rc"; then
            log "server copy is identical: nothing to do"
            exit 0
        fi
        die 4 "$FINAL on the server differs from this bundle. Bundles are immutable: build a new one (new bundle_id)."
        ;;
    absent) ;;
    *) die 5 "unexpected answer from the server: $STATE" ;;
esac

# ------------------------------------------------------------------------------------------------- 3. transfer
if [ "$DRY" -eq 1 ]; then
    log "--dry-run: would send to $SSH_TARGET:$INCOMING/ and publish as $FINAL"
    rsync -a -n --itemize-changes "${EXCLUDES[@]}" -e "$RSYNC_E" "$BUNDLE/" "$SSH_TARGET:$INCOMING/" || true
    exit 0
fi
log "sending to $SSH_TARGET:$INCOMING/ ..."
rsync -a --partial --delete "${EXCLUDES[@]}" -e "$RSYNC_E" "$BUNDLE/" "$SSH_TARGET:$INCOMING/" || die 5 "rsync failed"
rsync -a -e "$RSYNC_E" "$SUMS" "$SSH_TARGET:$INCOMING.sha256" || die 5 "rsync failed"
log "verifying on the server and publishing ..."
remote "set -e
cd $(q "$INCOMING")
sha256sum --quiet --strict -c $(q "$INCOMING.sha256")
find . -type d -exec chmod 755 {} +
find . -type f -exec chmod 444 {} +
mv -T $(q "$INCOMING") $(q "$FINAL")
rm -f $(q "$INCOMING.sha256")" || die 4 "server-side verification or publish failed. Nothing was published; $INCOMING is kept for a retry."

# ------------------------------------------------------------------------------------------------- 4. report
log "published $FINAL. Bundles on the server (newest first):"
remote "cd $(q "$DEST") && ls -1t | while read -r d; do [ -d \"\$d\" ] && printf '  %s  %s\n' \"\$(du -sh \"\$d\" | cut -f1)\" \"\$d\"; done" >&2 || true
log "activate: set WALK_BUNDLE_ID=$BUNDLE_ID in /opt/sloco-data/walk/walk.env, then on the server"
log "  docker compose --env-file /opt/sloco-data/walk/walk.env -f deploy/docker-compose.yml up -d walk-planner"
log "  curl -fsS http://127.0.0.1:18600/v1/health/ready && curl -fsS http://127.0.0.1:18600/v1/meta"
