import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { SearchController } from "./controllers/search.controller.js";
import { getSearchPlaces } from "./services/search.service.js";
import type { SearchPlacesService } from "./common/search.types.js";

export type SearchModuleOptions = {
  searchPlacesService?: SearchPlacesService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

export async function registerSearchModule(
  app: FastifyInstance,
  options: SearchModuleOptions = {}
) {
  const controller = new SearchController(
    options.searchPlacesService ?? getSearchPlaces,
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
