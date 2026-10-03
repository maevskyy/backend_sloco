import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { MapController } from "./controllers/map.controller.js";
import { createMapPlacesService } from "./services/map.service.js";
import { createMapTileService } from "./services/map-tile.service.js";
import { MapStore } from "./stores/map.store.js";
import { MapTileStore } from "./stores/map-tile.store.js";
import type { MapPlacesService } from "./common/map.types.js";
import type { MapTileService } from "./common/map.tiles.js";

export type MapModuleOptions = {
  mapPlacesService?: MapPlacesService;
  mapTileService?: MapTileService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerMapModule(
  app: FastifyInstance,
  options: MapModuleOptions = {}
) {
  const controller = new MapController(
    options.mapPlacesService ??
      createMapPlacesService(new MapStore(options.db)),
    options.mapTileService ??
      createMapTileService(new MapTileStore(options.db)),
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
