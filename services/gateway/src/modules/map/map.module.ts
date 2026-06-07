import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { MapController } from "./controllers/map.controller.js";
import { getMapPlaces } from "./services/map.service.js";
import { getMapTile } from "./services/map-tile.service.js";
import type { MapPlacesService } from "./common/map.types.js";
import type { MapTileService } from "./common/map.tiles.js";

export type MapModuleOptions = {
  mapPlacesService?: MapPlacesService;
  mapTileService?: MapTileService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

export async function registerMapModule(
  app: FastifyInstance,
  options: MapModuleOptions = {}
) {
  const controller = new MapController(
    options.mapPlacesService ?? getMapPlaces,
    options.mapTileService ?? getMapTile,
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
