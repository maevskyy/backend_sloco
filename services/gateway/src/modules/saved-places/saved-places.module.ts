import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { SavedPlacesController } from "./controllers/saved-places.controller.js";
import { createSavedPlacesService } from "./services/saved-places.service.js";
import { SavedPlacesStore } from "./stores/saved-places.store.js";
import type { SavedPlacesServiceContract } from "./common/saved-places.types.js";

export type SavedPlacesModuleOptions = {
  authService?: AuthService;
  savedPlacesService?: SavedPlacesServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerSavedPlacesModule(
  app: FastifyInstance,
  options: SavedPlacesModuleOptions = {}
) {
  const controller = new SavedPlacesController(
    options.savedPlacesService ??
      createSavedPlacesService(new SavedPlacesStore(options.db)),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
