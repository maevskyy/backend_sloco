import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import type { CacheStore } from "../../lib/cache/cache-store.js";
import { getCacheStore } from "../../lib/cache/index.js";
import { PlacesController } from "./controllers/places.controller.js";
import { createPlaceDetailsService } from "./services/places.service.js";
import type { PlaceDetailsService } from "./common/places.types.js";

export type PlacesModuleOptions = {
  placeDetailsService?: PlaceDetailsService;
  cacheStore?: CacheStore;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

export async function registerPlacesModule(
  app: FastifyInstance,
  options: PlacesModuleOptions = {}
) {
  const placeDetailsService =
    options.placeDetailsService ??
    createPlaceDetailsService(undefined, options.cacheStore ?? getCacheStore());
  const controller = new PlacesController(
    placeDetailsService,
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
