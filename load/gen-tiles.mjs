#!/usr/bin/env node

const DEFAULT_MIN_ZOOM = 10;
const DEFAULT_MAX_ZOOM = 16;

const bboxes = [
  {
    name: "bucharest_center_small",
    swLat: 44.4,
    swLng: 26.06,
    neLat: 44.46,
    neLng: 26.14
  },
  {
    name: "bucharest_center_large",
    swLat: 44.37,
    swLng: 26.02,
    neLat: 44.49,
    neLng: 26.18
  },
  {
    name: "bucharest_wide",
    swLat: 44.3,
    swLng: 25.9,
    neLat: 44.6,
    neLng: 26.3
  }
];

const minZoom = parseIntArg("--min-zoom", DEFAULT_MIN_ZOOM);
const maxZoom = parseIntArg("--max-zoom", DEFAULT_MAX_ZOOM);

if (minZoom > maxZoom) {
  throw new Error("--min-zoom must be less than or equal to --max-zoom");
}

const tiles = new Map();

for (const bbox of bboxes) {
  for (let z = minZoom; z <= maxZoom; z += 1) {
    const min = lngLatToTile(bbox.swLng, bbox.neLat, z);
    const max = lngLatToTile(bbox.neLng, bbox.swLat, z);

    for (let x = min.x; x <= max.x; x += 1) {
      for (let y = min.y; y <= max.y; y += 1) {
        tiles.set(`${z}/${x}/${y}`, { z, x, y, bbox: bbox.name });
      }
    }
  }
}

console.log("z,x,y,bbox");

for (const tile of [...tiles.values()].sort(compareTiles)) {
  console.log(`${tile.z},${tile.x},${tile.y},${tile.bbox}`);
}

function lngLatToTile(lng, lat, z) {
  const n = 2 ** z;
  const latRad = (clamp(lat, -85.05112878, 85.05112878) * Math.PI) / 180;
  const x = Math.floor(((lng + 180) / 360) * n);
  const y = Math.floor(
    ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
  );

  return {
    x: clamp(x, 0, n - 1),
    y: clamp(y, 0, n - 1)
  };
}

function parseIntArg(name, fallback) {
  const index = process.argv.indexOf(name);

  if (index === -1) {
    return fallback;
  }

  const value = Number(process.argv[index + 1]);

  if (!Number.isInteger(value)) {
    throw new Error(`${name} expects an integer`);
  }

  return value;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function compareTiles(a, b) {
  return a.z - b.z || a.x - b.x || a.y - b.y || a.bbox.localeCompare(b.bbox);
}
