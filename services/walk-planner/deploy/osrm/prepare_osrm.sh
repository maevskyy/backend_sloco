#!/usr/bin/env bash
# prepare_osrm.sh: build (or refresh) the OSRM foot-routing dataset of the walk-planner, test it, switch to it.
#
# Usage:  deploy/osrm/prepare_osrm.sh [options]     (host with Docker; run as the owner of --base, no sudo)
#   --region PATH         Geofabrik extract path (default europe/romania)
#   --bbox W,S,E,N        clip the extract to a lon/lat box with osmium. Bucharest: 25.80,44.20,26.40,44.70
#   --name NAME           dataset name prefix (default: region basename; with --bbox <basename>-clip;
#                         with --pbf the file name). Lowercase letters, digits, - and _.
#   --pbf FILE            build from a local .osm.pbf (no download, no md5 file; date = file mtime)
#   --base DIR            data root (default /opt/osrm): downloads/ datasets/ logs/ current previous
#   --image IMAGE         OSRM image (default ghcr.io/project-osrm/osrm-backend:v26.10.0-debian). MUST be the
#                         tag that serves the data (docker-compose.yml osrm-foot); change both together.
#   --threads N           threads for extract/partition/customize (default: all cores)
#   --build-mem SIZE      memory cap per build container (default 6g; exit 137 inside = out of memory)
#   --canary-port PORT    loopback port of the test server (default 5002)
#   --smoke "lon,lat;lon,lat[;...]"   smoke-route points (default: Piata Universitatii -> Macca-Villacrosse
#                         -> Radu Voda, Bucharest; pass your own for other regions)
#   --compose-file FILE   after switching, recreate this compose file's OSRM service and wait for health
#   --compose-env FILE    --env-file for docker compose (deploy/docker-compose.yml needs walk.env)
#   --service NAME        compose service to recreate (default osrm-foot)
#   --keep N              old datasets kept besides current (default 2)
#   --force               rebuild even if this dataset id exists
#   --no-switch           build + canary only; leave `current` untouched
#   --rollback            point `current` back to `previous` (and recreate with --compose-file); builds nothing.
#                         The dataset rolled away from gets a ROLLED_BACK marker: later runs will not switch to
#                         it again (cron-safe). Override: --rollback again, --force, or delete the marker.
#   -h, --help
#
# Steps (idempotent: the same extract again is a no-op, so it is safe from cron, e.g. monthly):
#   1. fetch <region>-latest.osm.pbf.md5 from Geofabrik; download the extract only if it changed (cached in
#      downloads/), verify the md5
#   2. --bbox: clip with osmium-tool (Debian trixie package, built once as the local image sloco-osmium-tool)
#   3. osrm-extract -p /opt/foot.lua --data_version <id> -> osrm-partition -> osrm-customize (MLD), with the
#      pinned OSRM image, no network, as your uid, under --build-mem; then the dataset is made world-readable
#      (chmod -R a+rX: directories 755, files 644 / 755) -- see "Users and permissions" below
#   4. canary: osrm-routed on 127.0.0.1:<canary port>, hardened EXACTLY like the compose osrm-foot service
#      (root user of the image, --cap-drop ALL, --security-opt no-new-privileges, --read-only, --tmpfs /tmp,
#      --init, the dataset mounted read-only); the smoke route must return code Ok, data_version = id, one leg
#      per point pair and sane distances. On failure the old dataset stays current.
#   5. move into datasets/<id>, then atomically switch current -> datasets/<id> (previous = old current);
#      with --compose-file, recreate the service (it re-resolves the symlink) and roll back if not healthy
#   6. prune: keep current + the --keep newest older datasets (+ previous), and the 20 newest log dirs
#
# Dataset id = <name>-<YYMMDD of the extract>, e.g. romania-260930. It becomes OSRM's data_version: every
# response carries it, and the walk-planner uses it as its leg-cache namespace and as versions.routing.
#
# Users and permissions (Linux hosts; Docker Desktop's macOS file sharing hides all of this):
#   - Run the script as the (non-root) owner of --base, e.g. a deploy user; no sudo. The build containers run
#     as that uid:gid (-u), so every file under --base stays owned by, and deletable by, that user.
#   - The osrm-foot service (deploy/docker-compose.yml) runs as the image's root user but with ALL capabilities
#     dropped: without CAP_DAC_OVERRIDE / CAP_DAC_READ_SEARCH that root can only read what the permission bits
#     grant to "other". osrm-extract writes some files owner-only (region.osrm.fileIndex: 0700), so the script
#     runs `chmod -R a+rX` on every new dataset; without it osrm-routed exits with
#     "File /data/region.osrm.fileIndex mapping failed: ... Permission denied".
#   - The canary runs with the compose service's hardening, so a dataset it accepts is one osrm-foot can serve.
#   - Datasets copied from elsewhere (rsync of datasets/<id>) need the same: chmod -R a+rX datasets/<id>.
#   - --base itself and datasets/ must be traversable by others (755, the default with umask 022).
#
# --bbox trade-off: a Bucharest clip (tens of MB instead of ~330 MB) builds in about a minute with < 1 GB RAM
# and serves with a few hundred MB. But routes cannot leave the box: points outside give NoSegment, and the
# planner then falls back to ORS/estimate, flagged per segment. Every new city also needs a new clip or merge.
# Romania-wide: est. 2.5-4 GB peak RAM to build (--build-mem 6g leaves headroom), about 1 GB to serve, and any
# Romanian city works. These are estimates; check `docker stats` on the first build.
#
# Host needs: bash, docker (daemon reachable as you), curl, md5sum, python3 (JSON checks, atomic rename),
# flock (optional, Linux), ~4 GB free under --base (1 GB with --bbox). One-time:
#   sudo install -d -m 755 -o "$USER" /opt/osrm
# Output: one line per step on stderr; each tool's full output goes to <base>/logs/<run>/<step>.log, and the
# last 25 lines are printed when a step fails.
# Exit codes: 0 ok / up to date · 2 usage or conflicting dataset · 3 prerequisites (tools, docker, base dir,
#   disk, image) · 4 download/integrity · 5 clip/extract/partition/customize failed · 6 canary failed (nothing
#   switched) · 7 switch/recreate failed (switched back) · 1 unexpected error

set -euo pipefail
umask 022

# ------------------------------------------------------------------------------------------------- defaults
OSRM_IMG="ghcr.io/project-osrm/osrm-backend:v26.10.0-debian"
REGION="europe/romania"
BBOX=""
NAME=""
PBF=""
BASE="/opt/osrm"
THREADS=""
BUILD_MEM="6g"
CANARY_PORT=5002
SMOKE="26.102500,44.435500;26.098698,44.433110;26.107657,44.423987"
COMPOSE_FILE=""
COMPOSE_ENV=""
SERVICE="osrm-foot"
KEEP=2
FORCE=0
NO_SWITCH=0
ROLLBACK=0
GEOFABRIK="${GEOFABRIK_URL:-https://download.geofabrik.de}"
OSMIUM_BASE="${OSMIUM_BASE_IMAGE:-debian:trixie-slim}"   # trixie: osmium-tool 1.18
OSMIUM_IMG="${OSMIUM_IMAGE:-sloco-osmium-tool:trixie}"

# ------------------------------------------------------------------------------------------------- helpers
log() { printf '%s [prepare_osrm] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
EXPECTED_EXIT=0
die() { local code=$1; shift; log "ERROR: $*"; EXPECTED_EXIT=1; exit "$code"; }
usage() { awk 'NR == 1 {next} /^#/ {sub(/^# ?/, ""); print; next} {exit}' "$0"; }
need_value() { [ $# -ge 2 ] && [ -n "$2" ] || die 2 "$1 needs a value (see --help)"; }
slug() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_-' '-' | sed -e 's/^-*//' -e 's/-*$//'; }
canon() { python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' "$1"; }
atomic_replace() { python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$1" "$2"; }   # rename(2)
current_target() { if [ -L "$BASE/current" ]; then canon "$BASE/current"; fi; }
previous_target() { if [ -L "$BASE/previous" ]; then canon "$BASE/previous"; fi; }

WORK=""          # build dir under datasets/ (removed unless published)
REPLACED=""      # old copy of a dataset rebuilt with --force (deleted after a successful switch)
CANARY=""        # canary container name (removed on exit)
cleanup() {
    local rc=$?
    if [ -n "$CANARY" ]; then docker rm -f "$CANARY" >/dev/null 2>&1 || true; fi
    if [ -n "$WORK" ] && [ -d "$WORK" ]; then rm -rf -- "$WORK"; fi
    if [ "$rc" -ne 0 ] && [ "$EXPECTED_EXIT" -eq 0 ]; then
        log "ERROR: unexpected failure (exit $rc); see the messages above${LOGDIR:+ and $LOGDIR}"
        exit 1
    fi
}
trap cleanup EXIT
trap 'log "interrupted"; EXPECTED_EXIT=1; exit 130' INT TERM

# run_step <label> <log file> <command...>: tool output -> log file; on failure print its tail and exit 5.
run_step() {
    local label=$1 logf=$2 t0=$SECONDS rc=0
    shift 2
    log "$label ..."
    "$@" >"$logf" 2>&1 || rc=$?
    if [ "$rc" -ne 0 ]; then
        tail -n 25 "$logf" | sed 's/^/    | /' >&2
        if [ "$rc" -eq 137 ]; then
            die 5 "$label: killed (exit 137 = out of memory under --build-mem $BUILD_MEM). Use --bbox, raise" \
                  "--build-mem, or build on a bigger machine and rsync datasets/<id>. Log: $logf"
        fi
        die 5 "$label failed (exit $rc). Log: $logf"
    fi
    log "$label: done in $((SECONDS - t0)) s"
}

ensure_image() {
    docker image inspect "$1" >/dev/null 2>&1 && return 0
    log "pulling $1 ..."
    docker pull -q "$1" >/dev/null || die 3 "cannot pull $1"
}

ensure_osmium_image() {
    docker image inspect "$OSMIUM_IMG" >/dev/null 2>&1 && return 0
    log "building $OSMIUM_IMG (osmium-tool from $OSMIUM_BASE) ..."
    printf 'FROM %s\nRUN apt-get update && apt-get install -y --no-install-recommends osmium-tool && rm -rf /var/lib/apt/lists/*\nENTRYPOINT ["osmium"]\n' \
        "$OSMIUM_BASE" | docker build -t "$OSMIUM_IMG" - >"$LOGDIR/0-osmium-image.log" 2>&1 \
        || die 3 "cannot build $OSMIUM_IMG (log: $LOGDIR/0-osmium-image.log)"
}

fetch_md5() {   # first field of a Geofabrik .md5 file, validated
    curl -fsS -L --retry 3 --retry-delay 5 --connect-timeout 20 "$1" | awk 'NR == 1 {print $1}' | grep -E '^[0-9a-f]{32}$'
}

file_md5() { md5sum "$1" | awk '{print $1}'; }
# make_readable <dir>: the dataset world-readable for the hardened osrm-routed (see "Users and permissions").
# Idempotent; repairs datasets built by older versions of this script. Nothing to do (and no chmod, which a
# non-owner could not run) when every directory is already traversable and every file readable by others.
make_readable() {
    if [ -z "$(find "$1" \( -type d ! -perm -o+rx \) -o \( ! -type d ! -perm -o+r \) 2>/dev/null | head -n 1)" ]; then
        return 0
    fi
    chmod -R a+rX "$1" || die 5 "cannot make $1 world-readable (chmod -R a+rX; run as the dataset's owner)"
}
file_yymmdd() { date -u -r "$1" +%y%m%d; }

# ------------------------------------------------------------------------------------------------- arguments
while [ $# -gt 0 ]; do
    case "$1" in
        --region) need_value "$@"; REGION=$2; shift 2 ;;
        --bbox) need_value "$@"; BBOX=$2; shift 2 ;;
        --name) need_value "$@"; NAME=$2; shift 2 ;;
        --pbf) need_value "$@"; PBF=$2; shift 2 ;;
        --base) need_value "$@"; BASE=$2; shift 2 ;;
        --image) need_value "$@"; OSRM_IMG=$2; shift 2 ;;
        --threads) need_value "$@"; THREADS=$2; shift 2 ;;
        --build-mem) need_value "$@"; BUILD_MEM=$2; shift 2 ;;
        --canary-port) need_value "$@"; CANARY_PORT=$2; shift 2 ;;
        --smoke) need_value "$@"; SMOKE=$2; shift 2 ;;
        --compose-file) need_value "$@"; COMPOSE_FILE=$2; shift 2 ;;
        --compose-env) need_value "$@"; COMPOSE_ENV=$2; shift 2 ;;
        --service) need_value "$@"; SERVICE=$2; shift 2 ;;
        --keep) need_value "$@"; KEEP=$2; shift 2 ;;
        --force) FORCE=1; shift ;;
        --no-switch) NO_SWITCH=1; shift ;;
        --rollback) ROLLBACK=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die 2 "unknown argument: $1 (see --help)" ;;
    esac
done

NUM='-?[0-9]+(\.[0-9]+)?'
[[ $REGION =~ ^[a-z0-9_-]+(/[a-z0-9_-]+)*$ ]] || die 2 "--region: bad Geofabrik path '$REGION' (e.g. europe/romania)"
if [ -n "$BBOX" ]; then
    [[ $BBOX =~ ^$NUM,$NUM,$NUM,$NUM$ ]] || die 2 "--bbox: expected W,S,E,N in degrees, got '$BBOX'"
    awk -F, '{ exit !($1 < $3 && $2 < $4 && $1 >= -180 && $3 <= 180 && $2 >= -90 && $4 <= 90) }' <<<"$BBOX" \
        || die 2 "--bbox: need W < E and S < N within lon/lat range, got '$BBOX'"
fi
[[ $SMOKE =~ ^$NUM,$NUM(\;$NUM,$NUM)+$ ]] || die 2 "--smoke: expected 'lon,lat;lon,lat[;...]', got '$SMOKE'"
[[ $CANARY_PORT =~ ^[0-9]+$ ]] && [ "$CANARY_PORT" -ge 1024 ] && [ "$CANARY_PORT" -le 65535 ] \
    || die 2 "--canary-port: 1024..65535"
[[ $KEEP =~ ^[0-9]$ ]] || die 2 "--keep: 0..9"
[ -z "$THREADS" ] || [[ $THREADS =~ ^[1-9][0-9]*$ ]] || die 2 "--threads: positive integer"
[[ $BUILD_MEM =~ ^[1-9][0-9]*[kmgKMG]?$ ]] || die 2 "--build-mem: e.g. 6g"
if [ -n "$COMPOSE_FILE" ] && [ ! -f "$COMPOSE_FILE" ]; then die 2 "--compose-file $COMPOSE_FILE: no such file"; fi
if [ -n "$COMPOSE_ENV" ] && [ ! -f "$COMPOSE_ENV" ]; then die 2 "--compose-env $COMPOSE_ENV: no such file"; fi
NPOINTS=$(($(printf '%s' "$SMOKE" | tr -cd ';' | wc -c) + 1))
THREAD_ARGS=()
if [ -n "$THREADS" ]; then THREAD_ARGS=(-t "$THREADS"); fi

# ------------------------------------------------------------------------------------------------- prerequisites
for cmd in docker curl md5sum python3 awk; do
    command -v "$cmd" >/dev/null 2>&1 || die 3 "missing command: $cmd"
done
docker info >/dev/null 2>&1 || die 3 "docker daemon not reachable (running? is $(id -un) allowed to use it?)"
[ -d "$BASE" ] && [ -w "$BASE" ] || die 3 "$BASE is missing or not writable. Once: sudo install -d -m 755 -o \"\$USER\" $BASE"
BASE=$(canon "$BASE")
if [ -e "$BASE/current" ] && [ ! -L "$BASE/current" ]; then
    die 3 "$BASE/current is a real directory, not a symlink. Docker creates one when a bind-mount source is" \
          "missing. Inspect it, remove it (rmdir), and run again."
fi
DATASETS="$BASE/datasets"
mkdir -p "$DATASETS" "$BASE/downloads" "$BASE/logs"
LOGDIR="$BASE/logs/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$LOGDIR"

if command -v flock >/dev/null 2>&1; then
    exec 9>"$BASE/.prepare_osrm.lock"
    flock -n 9 || die 3 "another prepare_osrm.sh run holds $BASE/.prepare_osrm.lock"
else
    log "warning: flock not found; concurrent runs are not prevented"
fi
# Build dirs of interrupted runs (the lock guarantees no other run is active). A .replaced-* dir (the old
# copy of a --force rebuild) is kept until a switch succeeds, so it can be restored by hand if one fails.
for d in "$DATASETS"/.build-*; do
    if [ -d "$d" ]; then log "removing leftover $d"; rm -rf -- "$d"; fi
done

# ------------------------------------------------------------------------------------------------- switching
# switch_to <dataset dir>: current -> it, previous -> the old current. Both are single rename(2) calls.
switch_to() {
    local target=$1 old
    make_readable "$target"
    old=$(current_target)
    ln -sfn -- "$target" "$BASE/.current.tmp"
    atomic_replace "$BASE/.current.tmp" "$BASE/current"
    if [ -n "$old" ] && [ "$old" != "$target" ]; then
        ln -sfn -- "$old" "$BASE/.previous.tmp"
        atomic_replace "$BASE/.previous.tmp" "$BASE/previous"
        log "current -> $(basename "$target") (previous: $(basename "$old"))"
    else
        log "current -> $(basename "$target")"
    fi
}

# recreate: re-create the compose service so its bind mount re-resolves `current`; wait for health.
recreate() {
    if [ -z "$COMPOSE_FILE" ]; then
        log "next: recreate the OSRM container so it mounts the new dataset, e.g."
        log "  docker compose --env-file <walk.env> -f <docker-compose.yml> up -d --no-deps --force-recreate $SERVICE"
        return 0
    fi
    local dc=(docker compose) cid status i
    if [ -n "$COMPOSE_ENV" ]; then dc+=(--env-file "$COMPOSE_ENV"); fi
    dc+=(-f "$COMPOSE_FILE")
    log "recreating $SERVICE ..."
    "${dc[@]}" up -d --no-deps --force-recreate "$SERVICE" >>"$LOGDIR/5-recreate.log" 2>&1 || return 1
    cid=$("${dc[@]}" ps -q "$SERVICE" 2>>"$LOGDIR/5-recreate.log") || return 1
    [ -n "$cid" ] || return 1
    for ((i = 0; i < 60; i++)); do
        status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo gone)
        case "$status" in
            healthy) log "$SERVICE is healthy"; return 0 ;;
            running) if [ "$i" -ge 5 ]; then log "$SERVICE is running (it has no healthcheck)"; return 0; fi ;;
            unhealthy | exited | dead | gone) docker logs --tail 25 "$cid" 2>&1 | sed 's/^/    | /' >&2 || true; return 1 ;;
        esac
        sleep 3
    done
    return 1
}

# switch_and_recreate <dataset dir>: switch, recreate; if the service does not come up, switch back (exit 7).
switch_and_recreate() {
    local target=$1 old old_prev
    old=$(current_target)
    old_prev=$(previous_target)
    switch_to "$target"
    if ! recreate; then
        if [ -n "$old" ] && [ "$old" != "$target" ]; then
            log "$SERVICE did not become healthy on $(basename "$target"); switching back to $(basename "$old")"
            switch_to "$old"
            if [ -n "$old_prev" ]; then   # `previous` as it was before this attempt, not the failed dataset
                ln -sfn -- "$old_prev" "$BASE/.previous.tmp"
                atomic_replace "$BASE/.previous.tmp" "$BASE/previous"
            fi
            recreate || log "WARNING: $SERVICE is not healthy on $(basename "$old") either; check it by hand"
        fi
        die 7 "switch failed (log: $LOGDIR/5-recreate.log)${REPLACED:+; the dataset replaced by --force is kept at $REPLACED}"
    fi
}

prune() {
    local cur prev d kept=0
    cur=$(current_target)
    prev=$(previous_target)
    # newest first; dataset names are [a-z0-9_-], so ls output is safe to read line by line
    while IFS= read -r d; do
        d=${d%/}
        [ -n "$d" ] || continue
        d=$(canon "$d")
        [ "$d" = "$cur" ] && continue
        if [ "$kept" -lt "$KEEP" ]; then kept=$((kept + 1)); continue; fi
        [ "$d" = "$prev" ] && continue
        log "pruning old dataset $(basename "$d")"
        rm -rf -- "$d"
    done < <(ls -1dt -- "$DATASETS"/*/ 2>/dev/null || true)
    { ls -1dt -- "$BASE/logs"/*/ 2>/dev/null || true; } | tail -n +21 | while IFS= read -r d; do rm -rf -- "${d%/}"; done
}

# ------------------------------------------------------------------------------------------------- rollback
if [ "$ROLLBACK" -eq 1 ]; then
    PREV=$(previous_target)
    [ -n "$PREV" ] && [ -d "$PREV" ] || die 2 "nothing to roll back to ($BASE/previous is missing or dangling)"
    LEFT=$(current_target)
    switch_and_recreate "$PREV"
    rm -f -- "$PREV/ROLLED_BACK"                       # explicitly chosen again
    if [ -n "$LEFT" ] && [ -d "$LEFT" ]; then
        date -u +%Y-%m-%dT%H:%M:%SZ >"$LEFT/ROLLED_BACK"   # later runs will not switch back to it on their own
    fi
    log "rolled back: current = $(basename "$PREV")${LEFT:+ ($(basename "$LEFT") is marked ROLLED_BACK)}"
    exit 0
fi

# ------------------------------------------------------------------------------------------------- 1. source
SRC=""; SRC_MD5=""; SRC_DATE=""; SRC_DESC=""
if [ -n "$PBF" ]; then
    [ -f "$PBF" ] && [ -r "$PBF" ] || die 2 "--pbf $PBF: not a readable file"
    SRC=$(canon "$PBF")
    SRC_DESC="file:$SRC"
    log "source: local file $SRC ($(du -h "$SRC" | awk '{print $1}')), hashing ..."
    SRC_MD5=$(file_md5 "$SRC")
    SRC_DATE=$(file_yymmdd "$SRC")
    DEFAULT_NAME=$(basename "$SRC")
    DEFAULT_NAME=$(slug "${DEFAULT_NAME%.pbf}")      # Cluj-Latest.osm.pbf -> cluj-latest-osm
    DEFAULT_NAME=${DEFAULT_NAME%-osm}
    DEFAULT_NAME=${DEFAULT_NAME%-latest}
else
    URL="$GEOFABRIK/$REGION-latest.osm.pbf"
    SRC="$BASE/downloads/$(printf '%s' "$REGION" | tr '/' '_')-latest.osm.pbf"
    SRC_DESC=$URL
    EXPECTED=$(fetch_md5 "$URL.md5") || die 4 "cannot fetch $URL.md5 (network down? wrong --region?)"
    if [ -f "$SRC" ] && [ -f "$SRC.md5" ] && [ -f "$SRC.date" ] && [ "$(cat "$SRC.md5")" = "$EXPECTED" ]; then
        log "source: cached $(basename "$SRC") is the current extract (md5 $EXPECTED)"
    else
        AVAIL_KB=$(df -Pk "$BASE" | awk 'NR == 2 {print $4}')
        [ "$AVAIL_KB" -gt 1500000 ] || die 3 "only $((AVAIL_KB / 1024)) MB free under $BASE (download needs ~1.5 GB headroom)"
        log "downloading $URL ..."
        T0=$SECONDS
        rm -f -- "$SRC.part"
        EFFECTIVE=$(curl -fsS -L --retry 3 --retry-delay 10 --connect-timeout 20 -R -o "$SRC.part" -w '%{url_effective}' "$URL") \
            || { rm -f -- "$SRC.part"; die 4 "download failed: $URL"; }
        ACTUAL=$(file_md5 "$SRC.part")
        if [ "$ACTUAL" != "$EXPECTED" ]; then
            # Geofabrik may have published a new extract between the two requests: re-read the md5 once.
            EXPECTED=$(fetch_md5 "$URL.md5" || true)
            [ "$ACTUAL" = "$EXPECTED" ] || { rm -f -- "$SRC.part"; die 4 "md5 mismatch for $URL (got $ACTUAL, expected $EXPECTED)"; }
        fi
        # Extract date: from the redirect target (romania-260930.osm.pbf), else from Last-Modified (curl -R).
        D6=""
        if [[ $EFFECTIVE =~ -([0-9]{6})\.osm\.pbf$ ]]; then D6=${BASH_REMATCH[1]}; fi
        [ -n "$D6" ] || D6=$(file_yymmdd "$SRC.part")
        mv -f -- "$SRC.part" "$SRC"
        printf '%s\n' "$ACTUAL" >"$SRC.md5"
        printf '%s\n' "$D6" >"$SRC.date"
        log "downloaded $(du -h "$SRC" | awk '{print $1}') in $((SECONDS - T0)) s: extract of $D6, md5 ok"
    fi
    SRC_MD5=$(cat "$SRC.md5")
    SRC_DATE=$(cat "$SRC.date")
    DEFAULT_NAME=${REGION##*/}
fi
if [ -z "$NAME" ]; then NAME=$DEFAULT_NAME${BBOX:+-clip}; fi
NAME=$(slug "$NAME")
[[ $NAME =~ ^[a-z0-9][a-z0-9_-]{0,47}$ ]] || die 2 "--name: lowercase letters, digits, - and _ (got '$NAME')"
DS="$NAME-$SRC_DATE"
FINAL="$DATASETS/$DS"
PARAMS=$(printf 'dataset=%s\nimage=%s\nprofile=/opt/foot.lua\nalgorithm=mld\nsource_md5=%s\nbbox=%s' \
    "$DS" "$OSRM_IMG" "$SRC_MD5" "${BBOX:-none}")
log "dataset $DS (source ${SRC_DESC}${BBOX:+, bbox $BBOX}; image $OSRM_IMG)"

# ------------------------------------------------------------------------------------------------- up to date?
SKIP_BUILD=0
if [ -d "$FINAL" ] && [ "$FORCE" -eq 0 ]; then
    if [ -f "$FINAL/BUILD_PARAMS" ] && [ "$(cat "$FINAL/BUILD_PARAMS")" = "$PARAMS" ]; then
        if [ "$(current_target)" = "$(canon "$FINAL")" ]; then
            make_readable "$FINAL"
            log "up to date: current = $DS"
            prune
            exit 0
        fi
        if [ -f "$FINAL/ROLLED_BACK" ]; then
            log "dataset $DS was rolled back on $(cat "$FINAL/ROLLED_BACK"): keeping current" \
                "($(basename "$(current_target)")). To use it anyway: --force, or delete $FINAL/ROLLED_BACK"
            exit 0
        fi
        log "dataset $DS is already built and was canary-tested: not rebuilding"
        SKIP_BUILD=1
    else
        die 2 "$FINAL exists but was built with other parameters (see its BUILD_PARAMS). Use --force to rebuild" \
              "it, or another --name."
    fi
fi

# ------------------------------------------------------------------------------------------------- 2-3. build
if [ "$SKIP_BUILD" -eq 0 ]; then
    NEED_KB=4000000
    if [ -n "$BBOX" ]; then NEED_KB=1000000; fi
    AVAIL_KB=$(df -Pk "$BASE" | awk 'NR == 2 {print $4}')
    [ "$AVAIL_KB" -gt "$NEED_KB" ] || die 3 "only $((AVAIL_KB / 1024)) MB free under $BASE; the build needs ~$((NEED_KB / 1024)) MB"
    ensure_image "$OSRM_IMG"
    WORK="$DATASETS/.build-$DS"
    rm -rf -- "$WORK"
    mkdir -p "$WORK"
    # As the invoking user (outputs stay deletable without sudo), no network, bounded memory.
    RUN=(docker run --rm -u "$(id -u):$(id -g)" -w /data --network none --memory "$BUILD_MEM" --security-opt no-new-privileges)
    T_BUILD=$SECONDS
    OSMIUM_VERSION=""
    if [ -n "$BBOX" ]; then
        ensure_osmium_image
        OSMIUM_VERSION=$(docker run --rm "$OSMIUM_IMG" --version 2>/dev/null | head -n 1 || true)
        run_step "1/4 clip to $BBOX (${OSMIUM_VERSION:-osmium})" "$LOGDIR/1-clip.log" \
            "${RUN[@]}" -v "$(dirname "$SRC"):/in:ro" -v "$WORK:/data" "$OSMIUM_IMG" \
            extract --bbox "$BBOX" --strategy complete_ways --overwrite -o /data/region.osm.pbf "/in/$(basename "$SRC")"
    else
        ln -- "$SRC" "$WORK/region.osm.pbf" 2>/dev/null || cp -- "$SRC" "$WORK/region.osm.pbf"
        log "1/4 no clip (whole extract)"
    fi
    run_step "2/4 osrm-extract (foot.lua, data_version $DS)" "$LOGDIR/2-extract.log" \
        "${RUN[@]}" -v "$WORK:/data" "$OSRM_IMG" \
        osrm-extract -p /opt/foot.lua --data_version "$DS" ${THREAD_ARGS[@]+"${THREAD_ARGS[@]}"} /data/region.osm.pbf
    run_step "3/4 osrm-partition" "$LOGDIR/3-partition.log" \
        "${RUN[@]}" -v "$WORK:/data" "$OSRM_IMG" osrm-partition ${THREAD_ARGS[@]+"${THREAD_ARGS[@]}"} /data/region.osrm
    run_step "4/4 osrm-customize" "$LOGDIR/4-customize.log" \
        "${RUN[@]}" -v "$WORK:/data" "$OSRM_IMG" osrm-customize ${THREAD_ARGS[@]+"${THREAD_ARGS[@]}"} /data/region.osrm
    rm -f -- "$WORK/region.osm.pbf"   # not needed to serve (the source stays in downloads/)
    OSRM_VERSION=$(docker run --rm "$OSRM_IMG" osrm-routed --version 2>/dev/null | head -n 1 || true)
    printf '%s\n' "$PARAMS" >"$WORK/BUILD_PARAMS"
    printf 'dataset=%s\nbuilt_at=%s\nbuilt_on=%s\nbuild_seconds=%s\nsource=%s\nsource_md5=%s\nbbox=%s\nimage=%s\nosrm_version=%s\nosmium=%s\nsize=%s\n' \
        "$DS" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname)" "$((SECONDS - T_BUILD))" "$SRC_DESC" "$SRC_MD5" \
        "${BBOX:-none}" "$OSRM_IMG" "${OSRM_VERSION:-?}" "${OSMIUM_VERSION:-none}" "$(du -sh "$WORK" | awk '{print $1}')" \
        >"$WORK/BUILD_INFO"
    # osrm-routed in the hardened container (root, no capabilities) reads only world-readable files:
    # osrm-extract leaves region.osrm.fileIndex owner-only (0700). See "Users and permissions" above.
    make_readable "$WORK"

    # --------------------------------------------------------------------------------------------- 4. canary
    log "canary: osrm-routed on 127.0.0.1:$CANARY_PORT ..."
    CANARY="osrm-canary-$$"
    # Hardened exactly like the compose osrm-foot service (deploy/docker-compose.yml): same image and command,
    # read-only root FS + tmpfs /tmp, all capabilities dropped, no-new-privileges, init, data mounted read-only.
    docker run -d --name "$CANARY" -p "127.0.0.1:$CANARY_PORT:5000" -v "$WORK:/data:ro" \
        --read-only --tmpfs /tmp:size=16m --cap-drop ALL --security-opt no-new-privileges --init \
        "$OSRM_IMG" osrm-routed --algorithm mld /data/region.osrm >/dev/null 2>"$LOGDIR/6-canary.log" \
        || die 6 "canary: cannot start the test server on 127.0.0.1:$CANARY_PORT (port in use? --canary-port)"
    FIRST=${SMOKE%%;*}
    READY=0
    for ((i = 0; i < 60; i++)); do
        if curl -fsS --max-time 2 "http://127.0.0.1:$CANARY_PORT/nearest/v1/foot/$FIRST" >/dev/null 2>&1; then
            READY=1
            break
        fi
        if [ "$(docker inspect -f '{{.State.Running}}' "$CANARY" 2>/dev/null || echo false)" != "true" ]; then
            docker logs --tail 25 "$CANARY" 2>&1 | sed 's/^/    | /' >&2 || true
            die 6 "canary: osrm-routed exited while loading $DS"
        fi
        sleep 2
    done
    [ "$READY" -eq 1 ] || die 6 "canary: no answer on 127.0.0.1:$CANARY_PORT after 120 s"
    RESP=$(curl -fsS --max-time 15 \
        "http://127.0.0.1:$CANARY_PORT/route/v1/foot/$SMOKE?overview=false&steps=false&generate_hints=false" 2>&1) \
        || die 6 "canary: smoke route failed: $RESP. Points outside this dataset? Pass --smoke for other regions."
    SUMMARY=$(python3 -c '
import json, sys
ds, n = sys.argv[1], int(sys.argv[2])
try:
    j = json.load(sys.stdin)
    assert j.get("code") == "Ok", "code=%r message=%r" % (j.get("code"), j.get("message"))
    assert j.get("data_version") == ds, "data_version=%r, expected %r" % (j.get("data_version"), ds)
    legs = j["routes"][0]["legs"]
    assert len(legs) == n - 1, "%d legs for %d points" % (len(legs), n)
    for i, leg in enumerate(legs):
        d, t = float(leg["distance"]), float(leg["duration"])
        assert 0 < d < 20000 and 0 < t < 4 * 3600, "leg %d: distance %.0f m, duration %.0f s" % (i, d, t)
except Exception as e:
    print("%s: %s" % (type(e).__name__, e))
    sys.exit(1)
print(", ".join("%.0f m / %.1f min" % (float(l["distance"]), float(l["duration"]) / 60) for l in legs))
' "$DS" "$NPOINTS" <<<"$RESP") || die 6 "canary: smoke route rejected ($SUMMARY); $DS was NOT switched to"
    log "canary ok: smoke route legs: $SUMMARY"
    docker rm -f "$CANARY" >/dev/null 2>&1 || true
    CANARY=""

    # publish: datasets/<id> appears complete or not at all
    if [ -d "$FINAL" ]; then REPLACED="$DATASETS/.replaced-$DS-$$"; atomic_replace "$FINAL" "$REPLACED"; fi   # --force
    atomic_replace "$WORK" "$FINAL"
    WORK=""
    log "built $DS ($(du -sh "$FINAL" | awk '{print $1}')) in $((SECONDS - T_BUILD)) s"
fi

# ------------------------------------------------------------------------------------------------- 5-6. switch
if [ "$NO_SWITCH" -eq 1 ]; then
    CUR=$(current_target)
    log "--no-switch: $DS is ready in $FINAL; current is unchanged (${CUR:+$(basename "$CUR")})"
    exit 0
fi
switch_and_recreate "$(canon "$FINAL")"
if [ -n "$REPLACED" ]; then rm -rf -- "$REPLACED"; fi
prune
log "done: current = $DS (logs: $LOGDIR)"
