import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import type { ReactionsServiceContract } from "./common/reactions.types.js";
import { ReactionsController } from "./controllers/reactions.controller.js";
import { createReactionsService } from "./services/reactions.service.js";
import { ReactionsStore } from "./stores/reactions.store.js";

export type ReactionsModuleOptions = {
  authService?: AuthService;
  reactionsService?: ReactionsServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerReactionsModule(
  app: FastifyInstance,
  options: ReactionsModuleOptions = {}
) {
  const controller = new ReactionsController(
    options.reactionsService ??
      createReactionsService(new ReactionsStore(options.db)),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
