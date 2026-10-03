import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import { MeController } from "./controllers/me.controller.js";
import { createMeService } from "./services/me.service.js";
import { MeStore } from "./stores/me.store.js";
import type { MeService } from "./common/me.types.js";

export type MeModuleOptions = {
  authService?: AuthService;
  meService?: MeService;
  savedPlacesService?: SavedPlacesService;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerMeModule(
  app: FastifyInstance,
  options: MeModuleOptions = {}
) {
  const controller = new MeController(
    options.meService ?? createMeService(new MeStore(options.db)),
    options.savedPlacesService ?? savedPlacesService,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
