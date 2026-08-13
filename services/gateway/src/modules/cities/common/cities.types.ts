export type CatalogCity = {
  name: string;
  country: string;
  placeCount: number;
};

export type CitiesResult = {
  cities: CatalogCity[];
};

export type CatalogCityRow = {
  name: string;
  country: string;
  place_count: number;
};

export type CitiesStoreContract = {
  listCatalogCities(): Promise<CatalogCityRow[]>;
};

export type CitiesServiceContract = {
  listCities(): Promise<CitiesResult>;
};
