export class PlaceNotFoundError extends Error {
  constructor(placeId: number) {
    super(`Place ${placeId} not found`);
    this.name = "PlaceNotFoundError";
  }
}

// A photo id the user did not upload. Until photo upload ships (SLO-70) no
// user has uploaded anything, so every id is unknown.
export class ReviewPhotoNotFoundError extends Error {
  constructor(readonly photoIds: string[]) {
    super(`Unknown review photo ids: ${photoIds.join(", ")}`);
    this.name = "ReviewPhotoNotFoundError";
  }
}
