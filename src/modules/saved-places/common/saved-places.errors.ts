export class PlaceNotFoundError extends Error {
  constructor(placeId: number) {
    super(`Place ${placeId} not found`);
    this.name = "PlaceNotFoundError";
  }
}

export class SavedCollectionNotFoundError extends Error {
  constructor(collectionId: string) {
    super(`Saved collection ${collectionId} not found`);
    this.name = "SavedCollectionNotFoundError";
  }
}

export class DefaultSavedCollectionDeleteError extends Error {
  constructor(collectionId: string) {
    super(`Default saved collection ${collectionId} cannot be deleted`);
    this.name = "DefaultSavedCollectionDeleteError";
  }
}

export class CollectionPlacesOrderError extends Error {
  constructor(collectionId: string) {
    super(`Invalid place order for collection ${collectionId}`);
    this.name = "CollectionPlacesOrderError";
  }
}
