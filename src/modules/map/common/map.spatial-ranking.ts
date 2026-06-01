import {
  deriveZoomFromBbox,
  scoreMapPlace,
  type MapRankingContext,
  type MapViewportBbox,
  type ScorablePlace
} from "./map.ranking.js";

export function rankSpatiallyBalancedMapPlaces<T extends ScorablePlace>(
  places: T[],
  context: MapRankingContext,
  bbox: MapViewportBbox,
  limit: number
): T[] {
  const safeLimit = Math.max(limit, 0);

  if (safeLimit === 0 || places.length === 0) {
    return [];
  }

  const gridSize = getMapGridSize(context.zoom ?? deriveZoomFromBbox(bbox));
  const cells = createGrid<T>(gridSize);

  for (const place of places) {
    cells[getCellIndex(place, bbox, gridSize)]?.push({
      place,
      score: scoreMapPlace(place, context)
    });
  }

  for (const cell of cells) {
    cell.sort((a, b) => b.score - a.score);
  }

  const selected: T[] = [];

  while (selected.length < safeLimit) {
    let pickedInRound = false;

    for (const cell of cells) {
      const next = cell.shift();

      if (!next) {
        continue;
      }

      selected.push(next.place);
      pickedInRound = true;

      if (selected.length >= safeLimit) {
        break;
      }
    }

    if (!pickedInRound) {
      break;
    }
  }

  return selected;
}

export function getMapGridSize(zoom: number): number {
  if (zoom <= 12) {
    return 4;
  }

  if (zoom <= 16) {
    return 5;
  }

  return 6;
}

function createGrid<T extends ScorablePlace>(gridSize: number) {
  return Array.from({ length: gridSize * gridSize }, () => [] as Array<{
    place: T;
    score: number;
  }>);
}

function getCellIndex(
  place: ScorablePlace,
  bbox: MapViewportBbox,
  gridSize: number
) {
  const latRatio = getAxisRatio(place.latitude, bbox.swLat, bbox.neLat);
  const lngRatio = getAxisRatio(place.longitude, bbox.swLng, bbox.neLng);
  const row = Math.min(Math.floor(latRatio * gridSize), gridSize - 1);
  const column = Math.min(Math.floor(lngRatio * gridSize), gridSize - 1);

  return row * gridSize + column;
}

function getAxisRatio(value: number, min: number, max: number) {
  const span = max - min;

  if (span <= 0) {
    return 0;
  }

  return Math.max(0, Math.min((value - min) / span, 1));
}
