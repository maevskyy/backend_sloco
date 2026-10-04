import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import {
  supabaseAuthService,
  type AuthService
} from "../auth/auth.service.js";
import { FeedStore } from "../feed/index.js";
import { WalksController } from "./controllers/walks.controller.js";
import { createWalksService } from "./services/walks.service.js";
import { WalksStore } from "./stores/walks.store.js";
import type { WalksServiceContract } from "./common/walks.types.js";

export type WalksModuleOptions = {
  authService?: AuthService;
  walksService?: WalksServiceContract;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerWalksModule(
  app: FastifyInstance,
  options: WalksModuleOptions = {}
) {
  const controller = new WalksController(
    options.walksService ??
      createWalksService(
        new WalksStore(options.db),
        // The feed's reading of saved places (favourites vs want-to-go,
        // "been there" not a favourite) — walks personalise the same way.
        new FeedStore(options.db)
      ),
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
