import type { FastifyInstance } from "fastify";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { MeController } from "./controllers/me.controller.js";
import { getMe } from "./services/me.service.js";
import type { MeService } from "./common/me.types.js";

export type MeModuleOptions = {
  authService?: AuthService;
  meService?: MeService;
  savedPlacesService?: SavedPlacesService;
};

export async function registerMeModule(
  app: FastifyInstance,
  options: MeModuleOptions = {}
) {
  const controller = new MeController(
    options.meService ?? getMe,
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
