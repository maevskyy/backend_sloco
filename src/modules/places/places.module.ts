import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { PlacesController } from "./controllers/places.controller.js";
import { getPlaceDetails } from "./services/places.service.js";
import type { PlaceDetailsService } from "./common/places.types.js";

export type PlacesModuleOptions = {
  placeDetailsService?: PlaceDetailsService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
};

export async function registerPlacesModule(
  app: FastifyInstance,
  options: PlacesModuleOptions = {}
) {
  const controller = new PlacesController(
    options.placeDetailsService ?? getPlaceDetails,
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
