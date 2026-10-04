#!/bin/sh
# Entrypoint of the walk-planner image: refuse to start the server without a valid data bundle, then exec it.
#
# Why: with WEB_CONCURRENCY > 1 a worker that fails at start-up stops uvicorn's supervisor, which then exits
# with code 0 (uvicorn's multi-worker behaviour; only a single worker exits with 3), so `docker ps`, restart
# policies and alerting could not tell a broken deployment from a clean stop. Checking the bundle(s) here
# first gives one clear message and exit code 78 (EX_CONFIG) for the common fatal causes:
#   - WALK_BUNDLE_DIR unset, or no manifest.json there (the bundle is not synced / not mounted);
#   - a bundle that fails the shallow validation: `python -m walk_planner bundle validate --shallow` on every
#     bundle WALK_BUNDLE_DIR names (a dir, "a,b", or a root -> newest per city): manifest + schema version,
#     every file listed and its size + sha256 (catches a corrupt or half-synced copy), catalog schema and row
#     count, interest file shapes and row alignment. About 0.3 s for Bucharest (60 MB hashed) + ~1 s imports.
# Deeper start-up checks (catalog load, taste warm-up, smoke plan) stay the service's own; they also stop the
# container, but with WEB_CONCURRENCY > 1 its exit code is then uvicorn's 0.
#
# Only the server command (uvicorn ...) is checked; anything else (python -m walk_planner ..., sh) runs as is.
# Output: nothing on success; on failure the validation report (JSON) and one line on stderr.
set -eu

if [ "${1:-}" = "uvicorn" ]; then
    bundle_dir="${WALK_BUNDLE_DIR:-}"
    if [ -z "$bundle_dir" ]; then
        echo "walk-planner: WALK_BUNDLE_DIR is not set (path of a data bundle, or of a directory of bundles)" >&2
        exit 78
    fi
    case "$bundle_dir" in
        *,*) ;;                                  # several bundles ("a,b"): the validation below checks each
        *)
            if [ ! -f "$bundle_dir/manifest.json" ] && ! ls "$bundle_dir"/*/manifest.json >/dev/null 2>&1; then
                echo "walk-planner: no data bundle at WALK_BUNDLE_DIR=$bundle_dir (expected $bundle_dir/manifest.json" \
                     "or $bundle_dir/<bundle_id>/manifest.json). Is the bundle synced (deploy/sync_bundle.sh) and" \
                     "mounted?" >&2
                exit 78
            fi
            ;;
    esac
    if ! report=$(python -m walk_planner bundle validate --shallow --json --compact "$bundle_dir" 2>&1); then
        printf '%s\n' "$report" >&2
        echo "walk-planner: the data bundle(s) at WALK_BUNDLE_DIR=$bundle_dir failed validation (report above):" \
             "refusing to start. Re-sync the bundle (deploy/sync_bundle.sh) or point WALK_BUNDLE_ID at a good one." >&2
        exit 78
    fi
fi

exec "$@"
