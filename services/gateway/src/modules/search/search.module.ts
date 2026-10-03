import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { SearchController } from "./controllers/search.controller.js";
import { createSearchPlacesService } from "./services/search.service.js";
import { SearchStore } from "./stores/search.store.js";
import type { SearchPlacesService } from "./common/search.types.js";

export type SearchModuleOptions = {
  searchPlacesService?: SearchPlacesService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerSearchModule(
  app: FastifyInstance,
  options: SearchModuleOptions = {}
) {
  const controller = new SearchController(
    options.searchPlacesService ??
      createSearchPlacesService(new SearchStore(options.db)),
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
