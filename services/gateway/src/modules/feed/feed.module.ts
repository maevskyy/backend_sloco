import type { FastifyInstance } from "fastify";
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
import type { FeedPlacesService } from "./common/feed.types.js";

export type FeedModuleOptions = {
  feedPlacesService?: FeedPlacesService;
  authService?: AuthService;
  savedPlacesService?: SavedPlacesService;
  reactionsService?: ReactionsService;
};

export async function registerFeedModule(
  app: FastifyInstance,
  options: FeedModuleOptions = {}
) {
  const feedPlacesService =
    options.feedPlacesService ??
    createFeedPlacesService(
      undefined,
      undefined,
      options.savedPlacesService ?? savedPlacesService,
      undefined,
      options.reactionsService ?? reactionsService
    );
  const controller = new FeedController(
    feedPlacesService ?? getFeedPlaces,
    options.authService ?? supabaseAuthService
  );

  controller.register(app);
}
