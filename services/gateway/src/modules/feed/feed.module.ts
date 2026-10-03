import type { FastifyInstance } from "fastify";
import type { Db } from "../../lib/db.js";
import { supabaseAuthService, type AuthService } from "../auth/auth.service.js";
import { FeedController } from "./controllers/feed.controller.js";
import {
  reactionsService,
  type ReactionsService
} from "../reactions/index.js";
import {
  savedPlacesService,
  type SavedPlacesService
} from "../saved-places/index.js";
import {
  createFeedPlacesService,
  getFeedPlaces
} from "./services/feed.service.js";
import { FeedStore } from "./stores/feed.store.js";
import { RecServedStore } from "./stores/rec-served.store.js";
import type { FeedPlacesService } from "./common/feed.types.js";

export type FeedModuleOptions = {
  feedPlacesService?: FeedPlacesService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
  reactionsService?: ReactionsService;
  // Shared direct-Postgres pool; stores fall back to getDb() without it.
  db?: Db;
};

export async function registerFeedModule(
  app: FastifyInstance,
  options: FeedModuleOptions = {}
) {
  const feedPlacesService =
    options.feedPlacesService ??
    createFeedPlacesService(
      new FeedStore(options.db),
      undefined,
      options.savedPlacesService ?? savedPlacesService,
      undefined,
      options.reactionsService ?? reactionsService,
      new RecServedStore(options.db)
    );
  const controller = new FeedController(
    feedPlacesService ?? getFeedPlaces,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
