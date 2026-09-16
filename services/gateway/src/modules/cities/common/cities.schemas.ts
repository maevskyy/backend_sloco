import { z } from "zod";

export const catalogCitySchema = z.object({
  name: z.string(),
  country: z.string(),
  placeCount: z.number().int().min(0)
});

export const citiesResponseSchema = z.object({
  cities: z.array(catalogCitySchema)
});

export const citiesSchemaRegistry = z.registry<{ id: string }>();

citiesSchemaRegistry.add(catalogCitySchema, { id: "CatalogCity" });
citiesSchemaRegistry.add(citiesResponseSchema, { id: "CitiesResponse" });
