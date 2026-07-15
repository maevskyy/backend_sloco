export class PlaceNotFoundError extends Error {
  constructor(placeId: number) {
    super(`Place ${placeId} not found`);
    this.name = "PlaceNotFoundError";
  }
}
