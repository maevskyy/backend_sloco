import type { FastifyInstance } from "fastify";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { SavedPlacesController } from "./controllers/saved-places.controller.js";
import {
  savedPlacesService
} from "./services/saved-places.service.js";
import type { SavedPlacesServiceContract } from "./common/saved-places.types.js";

export type SavedPlacesModuleOptions = {
  authService?: AuthService;
  savedPlacesService?: SavedPlacesServiceContract;
};

export async function registerSavedPlacesModule(
  app: FastifyInstance,
  options: SavedPlacesModuleOptions = {}
) {
  const controller = new SavedPlacesController(
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
