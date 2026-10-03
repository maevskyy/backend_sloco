import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  reactionsService,
  type ReactionsService
} from "../reactions/index.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import type { CacheStore } from "../../lib/cache/cache-store.js";
import { getCacheStore } from "../../lib/cache/index.js";
import { PlacesController } from "./controllers/places.controller.js";
import { createPlaceDetailsService } from "./services/places.service.js";
import { PlacesStore } from "./stores/places.store.js";
import type { PlaceDetailsService } from "./common/places.types.js";

export type PlacesModuleOptions = {
  placeDetailsService?: PlaceDetailsService;
  cacheStore?: CacheStore;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
  reactionsService?: ReactionsService;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerPlacesModule(
  app: FastifyInstance,
  options: PlacesModuleOptions = {}
) {
  const placeDetailsService =
    options.placeDetailsService ??
    createPlaceDetailsService(
      new PlacesStore(options.db),
      options.cacheStore ?? getCacheStore()
    );
  const controller = new PlacesController(
    placeDetailsService,
    options.savedPlacesService ?? savedPlacesService,
    options.reactionsService ?? reactionsService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
